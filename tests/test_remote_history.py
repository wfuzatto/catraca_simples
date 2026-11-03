"""Consultas remotas simuladas: não exigem servidor, webcam nem cartão real."""
import base64
import io
from datetime import datetime
from PIL import Image
import pytest
from remote_history import (
    CHECKINS_PATH, PHOTO_PATH, DETAIL_PATH, search_window,
    fetch_checkins, fetch_visitor_photo, normalize_checkin,
    decode_photo_response, visitor_details,
)
from hikcentral import HikError


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.calls = []
    def post(self, path, payload, **options):
        self.calls.append((path, payload, options))
        return self.data


def example_record(visitor="812", record="40", name="Maria Alves"):
    return {
        "recordId": record,
        "visitorStatus": "0",
        "visitorBaseInfo": {
            "visitorId": visitor, "fullName": name,
            "visitStartTime": "2026-11-02T12:57:00-03:00",
            "visitEndTime": "2026-11-04T17:00:00-03:00",
            "visitorGroup": "Hóspedes Vale", "remark": "Apto 18",
            "smallPhotoUrl": "opaque-photo-id",
        },
    }


def test_date_filter_includes_last_day_and_rejects_long_queries():
    start, end = search_window("01/11/2026", "02/11/2026")
    assert start == "2026-11-01T00:00:00-03:00"
    assert end == "2026-11-03T00:00:00-03:00"
    with pytest.raises(ValueError, match="32 dias"):
        search_window("01/01/2026", "02/03/2026")
    with pytest.raises(ValueError):
        search_window("02/11/2026", "01/11/2026")


def test_large_page_size_500_is_transmitted_and_501_is_rejected():
    client = FakeClient({"data": {"list": [], "totalNum": 0}})
    result = fetch_checkins(
        client, "2026-11-03T00:00:00-03:00",
        "2026-11-04T00:00:00-03:00", page=1, page_size=500)
    assert result["page_size"] == 500
    assert client.calls[0][1]["pageSize"] == 500
    with pytest.raises(ValueError, match="Página"):
        fetch_checkins(client, "2026-11-03T00:00:00-03:00",
                       "2026-11-04T00:00:00-03:00", page_size=501)


def test_paginated_checkin_allows_multiple_visits_for_same_visitor():
    records = [example_record(record="40"), example_record(record="41")]
    client = FakeClient({"code": "0", "data": {
        "list": records, "totalNum": 250,
    }})
    result = fetch_checkins(
        client, "2026-11-01T00:00:00-03:00", "2026-11-03T00:00:00-03:00",
        page=1, name="Maria")
    assert len(result["rows"]) == 2 and result["has_next"]
    assert all(r["door_ids"] == [] and r["access_names"] == [] for r in result["rows"])
    assert result["rows"][0]["group"] == "Hóspedes Vale"
    path, request, opts = client.calls[0]
    assert path == CHECKINS_PATH and request["orderType"] == "1"
    assert request["visitorName"] == "Maria" and not opts


def test_never_guess_missing_identifiers_or_privileges():
    assert normalize_checkin({"visitorBaseInfo": {}}) is None
    item = normalize_checkin(example_record())
    assert item["visitor_id"] == "812"
    assert item["registration_record_id"] == "40"
    assert item["photo_url"] == "opaque-photo-id"
    assert not item["access_names"] and not item["door_ids"]


def test_invalid_server_response_is_not_silently_treated_as_empty():
    client = FakeClient({"data": {"error": "not a list"}})
    with pytest.raises(HikError, match="lista paginada"):
        fetch_checkins(client, "2026-11-01T00:00:00-03:00",
                       "2026-11-03T00:00:00-03:00")


def test_detail_lookup_is_read_only():
    client = FakeClient({"data": {"visitorId": "812", "visitorGroupName": "Hóspedes Vale"}})
    assert visitor_details(client, "812")["visitorId"] == "812"
    assert client.calls[0][0] == DETAIL_PATH


def test_photo_download_passes_opaque_reference_only_to_artemis():
    output = io.BytesIO()
    Image.new("RGB", (40, 30)).save(output, "JPEG")
    data_uri = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    client = FakeClient({"code": "0", "data": data_uri})
    picture = fetch_visitor_photo(client, "812", "photo-ref")
    assert picture[:3] == b"\xff\xd8\xff"
    path, payload, options = client.calls[0]
    assert path == PHOTO_PATH
    assert payload == {"visitorId": "812", "picType": "0", "picUrl": "photo-ref"}
    assert options == {"allow_image_data": True}


def test_malicious_picture_and_bad_content_rejected():
    with pytest.raises(HikError):
        decode_photo_response("data:text/html;base64,SGVsbG8=")
    with pytest.raises(HikError):
        decode_photo_response("data:image/jpeg;base64," + base64.b64encode(b"not a jpeg").decode())
    with pytest.raises(HikError):
        fetch_visitor_photo(FakeClient({}), "812", "x"*260)


def test_remote_history_has_no_automatic_write_endpoints():
    import inspect
    import remote_history
    source = inspect.getsource(remote_history)
    assert "registerment/update" not in source
    assert "auth/reapplication" not in source
    assert "single/addPersons" not in source

def test_remote_register_time_is_not_the_visit_validity_time():
    row = example_record()
    row["registerTime"] = "2026-11-02T13:19:42-03:00"
    result = normalize_checkin(row)
    assert result["registered_at"] == "2026-11-02T13:19:42-03:00"
    assert result["entry_at"] == "2026-11-02T12:57:00-03:00"
    from remote_history import history_time
    assert history_time(result["registered_at"]) == "02/11/2026 13:19:42"
    assert history_time(result["entry_at"]) == "02/11/2026 12:57:00"
    assert history_time("") == "—"


def test_real_access_level_is_recovered_from_unique_matching_reservation():
    from remote_history import parse_access_levels, enrich_checkins_with_appointment_levels
    from remote_history import APPOINTMENTS_PATH
    record = normalize_checkin(example_record())
    appointment = {
        "appointID": "appt-9",
        "appointStartTime": record["entry_at"],
        "appointEndTime": record["exit_at"],
        "visitorInfo": {
            "visitorId": "812", "visitorGroupName": "Hóspedes Vale",
            "accessInfo": {"accessLevelList": [
                {"accessLevel": {"id": "level-3", "baseInfo": {"name": "VISITANTE_VALE"}}}
            ]},
        },
    }
    assert parse_access_levels(appointment) == [{"id": "level-3", "name": "VISITANTE_VALE"}]
    fake = FakeClient({"data": {"total": 1, "list": [appointment]}})
    output = enrich_checkins_with_appointment_levels(
        fake, [record], "2026-11-01T00:00:00-03:00", "2026-11-04T00:00:00-03:00")
    assert output[0]["access_names"] == ["VISITANTE_VALE"]
    assert output[0]["access_level_ids"] == ["level-3"]
    assert fake.calls[0][0] == APPOINTMENTS_PATH


def test_never_infer_level_from_group_on_ambiguous_or_unmatched_appointments():
    from remote_history import enrich_checkins_with_appointment_levels
    record = normalize_checkin(example_record())
    same_person_wrong_day = {
        "appointStartTime": "2026-11-01T12:57:00-03:00",
        "appointEndTime": record["exit_at"],
        "visitorInfo": {"visitorId": "812", "accessInfo": {"accessLevelList": [
            {"accessLevel": {"id": "3", "baseInfo": {"name": "VISITANTE_VALE"}}}]}},
    }
    fake = FakeClient({"data": {"total": 1, "list": [same_person_wrong_day]}})
    result = enrich_checkins_with_appointment_levels(
        fake, [record], "2026-11-01T00:00:00-03:00", "2026-11-04T00:00:00-03:00")
    assert result[0]["access_names"] == []
    assert result[0]["access_level_ids"] == []  # campo presente, mas sem nível inventado


def test_remote_edit_requires_original_checkin_id_and_level_id():
    from remote_history import resolve_remote_edit_record
    record = normalize_checkin(example_record())
    appointment = {
        "appointID": "9", "appointStartTime": record["entry_at"],
        "appointEndTime": record["exit_at"],
        "visitorInfo": {"visitorId": "812", "visitorGroupName": "Hóspedes Vale",
                        "accessInfo": {"accessLevelList": [
                            {"accessLevel": {"id": "10", "baseInfo": {"name": "VISITANTE_VALE"}}}]}},
    }
    class Client:
        def __init__(self, registered="40", level_id="10"):
            self.registered = registered
            self.level_id = level_id
            self.writes = []
        def find_existing_registration(self, *args):
            return self.registered
        def post(self, path, body, **kw):
            if path.endswith("appointmentlist"):
                return {"data": {"list": [appointment], "total": 1}}
            if path.endswith("single/visitorinfo"):
                return {"data": {"visitorId": "812"}}
            self.writes.append(path)
            raise AssertionError("O teste não pode alterar um visitante real.")
        def resolve_levels(self, names):
            return ([{"id": self.level_id, "name": "VISITANTE_VALE",
                      "elements": [{"Element": {"ID": str(i)}} for i in range(3)]}],
                    [str(i) for i in range(3)])

    verified, details = resolve_remote_edit_record(Client(), record)
    assert verified["_remote_verified"] is True
    assert verified["access_names"] == ["VISITANTE_VALE"]
    assert verified["door_ids"] == ["0", "1", "2"]
    assert details["visitorId"] == "812"
    with pytest.raises(HikError, match="check-in original mudou"):
        resolve_remote_edit_record(Client(registered="different"), record)
    with pytest.raises(HikError, match="ID do nível"):
        resolve_remote_edit_record(Client(level_id="different"), record)


def test_search_general_visitor_information_by_name_includes_person_without_checkin():
    from remote_history import DIRECTORY_PATH, search_visitor_directory
    client = FakeClient({"data": {"VisitorInfoList": {"VisitorInfo": [
        {"VisitorInfo": {
            "visitorId": "901", "visitorGivenName": "FABRICIO ROGERIO CAMPOS",
            "visitorFamilyName": "LEMOS", "visitorGroupName": "Hóspedes Vale",
            "smallPhotoUrl": "photo-ref",
        }},
        {"VisitorInfo": {
            "visitorId": "902", "visitorGivenName": "BRUNO",
            "visitorFamilyName": "SILVA", "visitorGroupName": "Day use",
        }},
    ]}, "total": 2}})
    rows = search_visitor_directory(client, "FABRICIO")
    assert len(rows) == 1
    assert rows[0]["visitor_id"] == "901"
    assert rows[0]["full_name"] == "FABRICIO ROGERIO CAMPOS LEMOS"
    assert rows[0]["checkin_state"] == "sem check-in no período"
    assert rows[0]["registration_record_id"] == ""
    assert rows[0]["entry_at"] == "" and rows[0]["exit_at"] == ""
    assert rows[0]["origin"] == "VisitorInformation"
    path, payload, options = client.calls[0]
    assert path == DIRECTORY_PATH
    assert payload["searchCriteria"] == {"personName": "FABRICIO"}
    assert payload["pageNo"] == 1 and not options


def test_directory_supports_list_and_never_guesses_visitor_id_from_name():
    from remote_history import search_visitor_directory
    client = FakeClient({"data": {"list": [
        {"VisitorInfo": {"visitorGivenName": "FABRICIO", "visitorFamilyName": "SILVA"}},
        {"VisitorInfo": {"visitorId": "123", "visitorGivenName": "FABRICIO", "visitorFamilyName": "LEMOS"}},
    ], "total": 2}})
    rows = search_visitor_directory(client, "fabricio")
    assert [x["visitor_id"] for x in rows] == ["123"]


def test_remote_edit_blocked_for_visitor_already_checked_out():
    from remote_history import resolve_remote_edit_record
    checked_out = normalize_checkin(example_record())
    checked_out["checkin_state"] = "saída no terminal"
    class FailIfCalled:
        def post(self, *args, **kwargs):
            raise AssertionError("Visitante encerrado não deve ser atualizado.")
        def find_existing_registration(self, *args):
            raise AssertionError("Não reabrir check-in anterior.")
    with pytest.raises(HikError, match="Registrar novamente"):
        resolve_remote_edit_record(FailIfCalled(), checked_out)


def test_active_visitor_can_use_exact_level_from_visitor_information_when_appointment_missing():
    from remote_history import resolve_remote_edit_record
    record = normalize_checkin(example_record())
    class ExistingVisitor:
        def find_existing_registration(self, *args):
            return "40"
        def post(self, path, payload):
            if path.endswith("appointmentlist"):
                return {"data": {"list": [], "total": 0}}
            if path.endswith("single/visitorinfo"):
                return {"data": {
                    "visitorId": "812", "visitorGroupName": "Hóspedes Vale",
                    "accessInfo": {"accessLevelList": [
                        {"accessLevel": {"id": "10", "baseInfo": {"name": "VISITANTE_VALE"}}}
                    ]}}}
            raise AssertionError("Não deve haver nenhum POST de escrita.")
        def resolve_levels(self, names):
            assert names == ["VISITANTE_VALE"]
            return ([{"id": "10", "name": "VISITANTE_VALE",
                      "elements": [{"Element": {"ID": str(i)}} for i in range(3)]}],
                    [str(i) for i in range(3)])
    verified, details = resolve_remote_edit_record(ExistingVisitor(), record)
    assert verified["_remote_verified"] is True
    assert verified["_level_verified_from"] == "Visitor Information"
    assert verified["access_level_ids"] == ["10"]
    assert details["visitorId"] == "812"


def test_active_visitor_with_unverified_level_stays_readonly():
    from remote_history import resolve_remote_edit_record
    record = normalize_checkin(example_record())
    class MissingLevel:
        def find_existing_registration(self, *args):
            return "40"
        def post(self, path, payload):
            if path.endswith("appointmentlist"):
                return {"data": {"list": [], "total": 0}}
            if path.endswith("single/visitorinfo"):
                return {"data": {"visitorId": "812", "visitorGroupName": "Hóspedes Vale"}}
            raise AssertionError("Never call write endpoints")
    with pytest.raises(HikError, match="não confirmou um único nível"):
        resolve_remote_edit_record(MissingLevel(), record)
