from copy import deepcopy

import pytest

from edit_errors import EditorFieldError
from hikcentral import HikCentral, HikError
from remote_history import APPOINTMENTS_PATH, DETAIL_PATH
from unregistered_reservation import UPDATE_BOOKING, reschedule_unregistered_reservation


OLD_START = "2099-11-11T00:00:00-03:00"
OLD_END = "2099-11-11T23:59:59-03:00"
NEW_START = "2099-11-12T00:00:00-03:00"
NEW_END = "2099-11-12T23:59:59-03:00"
LEVEL = {"id": "7", "name": "VISITANTE_VALE"}
GROUP = "Hóspedes Vale"
VISITOR = "2023"
CODE = "6119"
APPOINTMENT = "774452117501902800"
NEW_APPOINTMENT = "774452117501902848"


class ReservationServer:
    url = "https://hikcentral.test"
    access_info = staticmethod(HikCentral.access_info)

    def __init__(self):
        self.booking = {
            "appointID": APPOINTMENT, "appointCode": CODE, "appointStatus": 0,
            "appointStartTime": OLD_START, "appointEndTime": OLD_END,
            "receptionistId": "10", "visitReasonType": 4, "visitReasonDetail": "Hotel",
            "visitorInfo": {
                "visitorId": VISITOR, "remark": "321", "visitorGroupName": GROUP,
                "visitorGivenName": "Teste", "visitorFamilyName": "Sintético",
                "faces": [{"faceData": "credential-never-sent"}],
                "cards": [{"cardNo": "999"}],
                "accessInfo": {"electrostaticDetectionType": 0, "qrCodeValidNum": 12},
            },
            "accessInfo": self.access_info([LEVEL]),
        }
        self.writes = []
        self.record = None
        self.reply_error = False
        self.defer_readback = False
        self.update = None
        self.profile = {
            "visitorId": VISITOR, "visitorGroupName": GROUP, "companyName": "Vale",
            "visitorGivenName": "Outro nome de perfil", "visitorFamilyName": "Teste",
        }

    def post(self, path, payload):
        if path == APPOINTMENTS_PATH:
            assert payload["appointCode"] == CODE
            return {"data": {"list": [deepcopy(self.booking)], "total": 1}}
        if path == DETAIL_PATH:
            assert payload["visitorId"] == VISITOR
            return {"data": {"VisitorInfo": deepcopy(self.profile)}}
        assert path == UPDATE_BOOKING
        self.writes.append(deepcopy(payload))
        info = payload["visitorInfoList"][0]["VisitorInfo"]
        self.update = {
            **self.booking, "appointID": NEW_APPOINTMENT, "appointStartTime": payload["appointStartTime"],
            "appointEndTime": payload["appointEndTime"],
            "visitorInfo": {**self.booking["visitorInfo"], **info},
        }
        if not self.defer_readback:
            self.booking = self.update
        if self.reply_error:
            raise HikError("A resposta do teste foi perdida.")
        return {"data": {"appointRecordId": NEW_APPOINTMENT}}

    def find_registration_record(self, visitor_id, start, end, *, active_only=False):
        assert visitor_id == VISITOR
        return self.record


@pytest.fixture
def lab(tmp_path):
    server = ReservationServer()
    record = {
        "visitor_id": VISITOR, "appointment_id": APPOINTMENT, "reservation_code": CODE,
        "entry_at": OLD_START, "exit_at": OLD_END, "group": GROUP,
    }
    journal = tmp_path / "reserved.json"
    return server, record, journal


def test_updates_verified_reservation_before_checkin_and_accepts_reissued_id(lab):
    server, record, journal = lab
    result = reschedule_unregistered_reservation(
        server, record, NEW_START, NEW_END, "321-A", [LEVEL], journal=journal)

    body, = server.writes
    assert (body["appointRecordId"], body["receptionistId"], body["visitReasonDetail"]) == (APPOINTMENT, "10", "Hotel")
    assert body["accessInfo"] == server.access_info([LEVEL])
    assert body["visitorInfoList"] == [{"VisitorInfo": {
        "visitorId": VISITOR, "visitorGivenName": "Teste", "visitorFamilyName": "Sintético",
        "visitorGroupName": GROUP, "companyName": "Vale", "remark": "321-A", "gender": 0,
        "accessInfo": {"electrostaticDetectionType": 0, "qrCodeValidNum": 12},
    }}]
    assert result["state"] == "confirmed"
    assert result["operation"] == "reservation_update"
    assert result["confirmed_appointment_id"] == NEW_APPOINTMENT
    assert result["confirmed_visitor_id"] == VISITOR
    assert result["reservation_period"] == {
        "state": "matched", "appointment_id": NEW_APPOINTMENT, "code": CODE,
        "entry_at": NEW_START, "exit_at": NEW_END,
    }
    assert server.booking["appointStatus"] == 0
    assert not journal.exists()


def test_updates_remark_on_same_dates(lab):
    server, record, journal = lab
    result = reschedule_unregistered_reservation(
        server, record, OLD_START, OLD_END, "Novo", [LEVEL], journal=journal)
    assert result["reservation_period"]["entry_at"] == OLD_START
    assert server.booking["visitorInfo"]["remark"] == "Novo"
    assert len(server.writes) == 1


def test_keeps_verified_access_level_when_server_omits_group(lab):
    server, record, journal = lab
    server.booking["visitorInfo"].pop("visitorGroupName")
    server.profile.pop("visitorGroupName")
    record["group"] = ""

    result = reschedule_unregistered_reservation(
        server, record, NEW_START, NEW_END, "321-A", [LEVEL], journal=journal)

    body, = server.writes
    assert "visitorGroupName" not in body["visitorInfoList"][0]["VisitorInfo"]
    assert body["accessInfo"] == server.access_info([LEVEL])
    assert result["confirmed_visitor_id"] == VISITOR


@pytest.mark.parametrize("reason", ["status", "linked", "stale", "other_visitor", "local_checkin"])
def test_refuses_invalid_target_before_any_mutation(lab, reason):
    server, record, journal = lab
    if reason == "status":
        server.booking["appointStatus"] = 3
    elif reason == "linked":
        server.record = {"recordId": "other", "visitorStatus": "0"}
    elif reason == "stale":
        server.booking["appointID"] = "123"
    elif reason == "other_visitor":
        server.booking["visitorInfo"]["visitorId"] = "999"
    else:
        record["registration_record_id"] = "checkin"
    with pytest.raises((EditorFieldError, HikError)):
        reschedule_unregistered_reservation(
            server, record, NEW_START, NEW_END, "321-A", [LEVEL], journal=journal)
    assert not server.writes
    assert not journal.exists()


def test_reconciles_uncertain_update_without_resending(lab):
    server, record, journal = lab
    server.reply_error = True
    server.defer_readback = True
    with pytest.raises(HikError, match="não confirmou"):
        reschedule_unregistered_reservation(
            server, record, NEW_START, NEW_END, "321-A", [LEVEL], journal=journal)
    assert journal.exists()

    with pytest.raises(HikError, match="edição anterior"):
        reschedule_unregistered_reservation(
            server, record, NEW_START, NEW_END, "321-A", [LEVEL], journal=journal)
    server.booking = server.update
    result = reschedule_unregistered_reservation(
        server, record, NEW_START, NEW_END, "321-A", [LEVEL], journal=journal)
    assert result["confirmed_appointment_id"] == NEW_APPOINTMENT
    assert len(server.writes) == 1
    assert not journal.exists()
