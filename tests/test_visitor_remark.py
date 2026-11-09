from copy import deepcopy

import pytest

from edit_errors import EditorFieldError
from hikcentral import HikError
from remote_history import APPOINTMENTS_PATH, DETAIL_PATH
from visitor_remark import UPDATE_BOOKING, update_pending_visitor_remark


START = "2026-11-10T02:05:00-03:00"
END = "2026-11-10T02:20:00-03:00"
RECORD = {"visitor_id": "2118", "appointment_id": "1000", "reservation_code": "2735",
          "reservation_entry_at": START, "reservation_exit_at": END}


class FakeHikCentral:
    def __init__(self):
        self.booking = {
            "appointID": "1000", "appointCode": "2735", "appointStatus": 0,
            "appointStartTime": START, "appointEndTime": END, "visitReasonType": 2,
            "accessInfo": {"accessLevelList": [], "qrCodeValidNum": 7},
            "visitorInfo": {"visitorId": "2118", "visitorGivenName": "Nome anterior",
                            "visitorFamilyName": "Visitante", "remark": "123"},
        }
        self.profile = {**self.booking["visitorInfo"], "visitorGivenName": "Nome atualizado"}
        self.active = None
        self.writes = []
        self.confirm_profile = True
        self.lost_response = False

    def find_registration_record(self, vid, start, end):
        assert (vid, start, end) == ("2118", START, END)
        return self.active

    def post(self, path, request):
        if path == APPOINTMENTS_PATH:
            assert request["appointCode"] == "2735"
            return {"data": {"list": [deepcopy(self.booking)]}}
        if path == DETAIL_PATH:
            return {"data": {"VisitorInfo": deepcopy(self.profile)}}
        assert path == UPDATE_BOOKING
        self.writes.append(request)
        updated = request["visitorInfoList"][0]["VisitorInfo"]
        self.booking["appointID"] = "2000"
        self.booking["visitorInfo"].update(updated)
        if self.confirm_profile:
            self.profile.update(updated)
        if self.lost_response:
            raise HikError("Resposta perdida")
        return {"data": {"appointRecordId": "2000"}}


def test_updates_remark_on_pending_visitor_and_keeps_period_and_access(tmp_path):
    client = FakeHikCentral()
    journal = tmp_path / "remark.json"
    period = update_pending_visitor_remark(client, RECORD, "321", journal=journal)

    assert period["appointment_id"] == "2000"
    assert period["code"] == "2735"
    assert not journal.exists()
    [request] = client.writes
    assert request["appointRecordId"] == "1000"
    assert (request["appointStartTime"], request["appointEndTime"]) == (START, END)
    assert request["accessInfo"] == {"accessLevelList": [], "qrCodeValidNum": 7}
    info = request["visitorInfoList"][0]["VisitorInfo"]
    assert (info["visitorId"], info["remark"], info["visitorGivenName"]) == ("2118", "321", "Nome atualizado")
    assert not {"cards", "faces", "visitorGroupName"}.intersection(info)


def test_requires_visitor_profile_readback_and_reconciles_without_second_write(tmp_path):
    client = FakeHikCentral()
    client.confirm_profile = False
    journal = tmp_path / "remark.json"

    with pytest.raises(EditorFieldError, match="não confirmou"):
        update_pending_visitor_remark(client, RECORD, "321", journal=journal)
    assert journal.exists()
    assert "321" not in journal.read_text(encoding="utf-8")
    assert client.booking["visitorInfo"]["remark"] == "321"
    assert client.profile["remark"] == "123"

    client.profile["remark"] = "321"
    period = update_pending_visitor_remark(client, RECORD, "321", journal=journal)
    assert period["appointment_id"] == "2000"
    assert len(client.writes) == 1


def test_reconciles_lost_response(tmp_path):
    client = FakeHikCentral()
    client.lost_response = True
    period = update_pending_visitor_remark(client, RECORD, "321", journal=tmp_path / "remark.json")
    assert period["appointment_id"] == "2000"
    assert len(client.writes) == 1


def test_refuses_to_write_if_checkin_appeared(tmp_path):
    client = FakeHikCentral()
    client.active = {"recordId": "301"}
    with pytest.raises(EditorFieldError, match="check-in"):
        update_pending_visitor_remark(client, RECORD, "321", journal=tmp_path / "remark.json")
    assert client.writes == []
