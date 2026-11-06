"""Regressões de recuperação de edição; API simulada, sem credenciais reais."""
import base64
import io

import pytest
from PIL import Image

from hikcentral import HikError
from remote_history import (
    normalize_checkin, parse_access_levels, resolve_remote_edit_record,
    fetch_visitor_photo, PHOTO_PATH,
)


def active_checkin(status="0"):
    return normalize_checkin({
        "recordId": "check-1009",
        "visitorStatus": status,
        "visitorBaseInfo": {
            "visitorId": "1009", "fullName": "Wesley Teste",
            "visitorGroup": "Hóspedes Vale",
            "visitStartTime": "2099-11-03T12:20:00-03:00",
            "visitEndTime": "2099-11-03T17:00:00-03:00",
        }
    })


def single_level():
    return {"id": "10", "name": "VISITANTE_VALE",
            "elements": [{"Element": {"ID": x}} for x in ("entrada", "saida", "servico")]}


class FakeHik:
    def __init__(self, details=None, appointment=None, level=None, status="0"):
        self.details = details or {"visitorId": "1009", "visitorGroupName": "Hóspedes Vale"}
        self.appointment = appointment or {"data": {"list": [], "total": 0}}
        self.level = level or single_level()
        self.calls = []
        self.status = status
    def find_existing_registration(self, vid, start, end):
        assert vid == "1009"
        return "check-1009"
    def find_registration_record(self, vid, start, end, *, active_only=False, record_id=""):
        assert active_only is False
        found = self.find_existing_registration(vid, start, end)
        if record_id and record_id != found:
            return None
        return {"recordId": found, "visitorStatus": self.status}
    def post(self, path, payload, **kw):
        self.calls.append(path)
        if path.endswith("single/visitorinfo"):
            return {"data": self.details}
        if path.endswith("appointmentlist"):
            return self.appointment
        raise AssertionError("No write endpoint allowed: " + path)
    def resolve_levels(self, requested):
        assert requested == ["VISITANTE_VALE"]
        return [self.level], ["entrada", "saida", "servico"]


def test_group_approved_fallback_needs_operator_consent_when_api_omits_levels():
    api = FakeHik()
    record, details = resolve_remote_edit_record(api, active_checkin())
    assert record["_remote_verified"] is True
    assert record["_requires_access_confirmation"] is True
    assert record["access_names"] == ["VISITANTE_VALE"]
    assert record["access_level_ids"] == ["10"]
    assert len(record["door_ids"]) == 3
    assert record["visitor_id"] == "1009"
    assert record["registration_record_id"] == "check-1009"
    assert not any("/update" in call or "/addPersons" in call for call in api.calls)


def test_real_level_from_checkin_is_sufficient_and_requires_no_access_recovery():
    row = active_checkin()
    row["access_names"] = ["VISITANTE_VALE"]
    row["access_level_ids"] = ["10"]
    edited, _ = resolve_remote_edit_record(FakeHik(), row)
    assert edited["_requires_access_confirmation"] is False
    assert "check-in" in edited["_level_verified_from"]


def test_access_level_parser_reads_access_info_from_checkin_and_nested_details():
    info = {"accessInfo": {"accessLevelList": [
        {"accessLevel": {"id": 10, "baseInfo": {"name": "VISITANTE_VALE"}}}
    ]}}
    assert parse_access_levels(info) == [{"id": "10", "name": "VISITANTE_VALE"}]
    assert parse_access_levels({"VisitorInfo": info}) == parse_access_levels(info)
    assert parse_access_levels({"visitorBaseInfo": info}) == parse_access_levels(info)


def test_mismatch_does_not_allow_using_group_mapping():
    row = active_checkin()
    row["access_names"] = ["VISITANTE_SERRA"]
    row["access_level_ids"] = ["55"]
    with pytest.raises(HikError, match="corresponde|difere|diverge"):
        resolve_remote_edit_record(FakeHik(), row)


def test_wrong_visitor_id_or_checkin_id_blocks_without_remote_writes():
    api = FakeHik(details={"visitorId": "999", "visitorGroupName": "Hóspedes Vale"})
    with pytest.raises(HikError, match="Visitor ID"):
        resolve_remote_edit_record(api, active_checkin())
    row = active_checkin()
    row["registration_record_id"] = "different"
    with pytest.raises(HikError, match="check-in original"):
        resolve_remote_edit_record(FakeHik(), row)


@pytest.mark.parametrize("status", ["1", "2", "3", "4"])
def test_closed_registration_can_be_prepared_for_editing_without_writes(status):
    api = FakeHik(status=status)
    row = active_checkin(status)
    verified, _ = resolve_remote_edit_record(api, row)
    assert verified["_remote_verified"] is True
    assert verified["registration_record_id"] == "check-1009"
    assert not any("/update" in path or "/registerment" in path for path in api.calls)


def test_photo_tries_detail_reference_after_first_reference_fails():
    buf = io.BytesIO()
    Image.new("RGB", (25, 25), "white").save(buf, "JPEG")
    encoded = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    class PhotoAPI:
        def __init__(self):
            self.refs = []
        def post(self, path, payload, **kwargs):
            assert path == PHOTO_PATH and kwargs == {"allow_image_data": True}
            self.refs.append(payload.get("picUrl", ""))
            if payload.get("picUrl") == "expired-photo-url":
                raise HikError("HikCentral code 128")
            return {"data": encoded}
    api = PhotoAPI()
    output = fetch_visitor_photo(
        api, "1009", "expired-photo-url",
        details={"VisitorInfo": {"smallPhotoUrl": "current-photo-ref"}})
    assert output.startswith(b"\xff\xd8\xff")
    assert api.refs[:2] == ["expired-photo-url", "current-photo-ref"]


def test_failed_photo_returns_actionable_error_without_faking_image():
    class PhotoAPI:
        def post(self, path, payload, **kwargs):
            raise HikError("HikCentral recusou foto: código 128")
    with pytest.raises(HikError, match="selecionar uma foto nova"):
        fetch_visitor_photo(PhotoAPI(), "1009", "old-ref")


def test_editor_uses_original_group_even_when_station_preferred_is_different():
    """Preferência para NOVO cadastro não redefine grupo de visitante existente."""
    from visitor_editor import selected_editor_access
    from core import default_settings
    settings = default_settings()
    settings["preferred_group"] = "Hóspedes Serra"
    available = ["Hóspedes Serra", "Hóspedes Vale", "Day use"]
    assert selected_editor_access("Hóspedes Vale", available, settings) == [
        "VISITANTE_VALE"]
    with pytest.raises(ValueError, match="Grupo"):
        selected_editor_access("Grupo Desconhecido", available, settings)
