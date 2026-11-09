"""Regressões da emissão NFC no VisitorInfo, sem contato com o HikCentral."""
from copy import deepcopy

import pytest

from edit_errors import EditorFieldError
from hikcentral import HikCentral, HikError
from remote_history import APPOINTMENTS_PATH, DETAIL_PATH
from unregistered_reservation import UPDATE_BOOKING
from visitor_profile import PERSON_INFO, PERSON_UPDATE
from visitor_card_sync import (
    update_reserved_visitor_card, verify_checkedin_visitor_card)


START = "2099-11-11T00:00:00-03:00"
END = "2099-11-11T23:59:59-03:00"
VISITOR = "2203"
APPT = "774452117501902800"
CODE = "6119"
CARD = "43123"
GROUP = "Hóspedes Vale"
LEVEL = {"id": "7", "name": "VISITANTE_VALE"}


def record():
    return {
        "visitor_id": VISITOR, "appointment_id": APPT, "reservation_code": CODE,
        "reservation_entry_at": START, "reservation_exit_at": END, "group": GROUP,
        "card_number": CARD,
    }


class Api:
    url = "https://hikcentral.test"
    access_info = staticmethod(HikCentral.access_info)

    def __init__(self):
        self.booking = {
            "appointID": APPT, "appointCode": CODE, "appointStatus": 0,
            "appointStartTime": START, "appointEndTime": END,
            "receptionistId": "", "visitReasonType": 4,
            "visitReasonDetail": "Hotel", "accessInfo": self.access_info([LEVEL]),
            "visitorInfo": {
                "visitorId": VISITOR, "visitorGivenName": "Wesley1",
                "visitorFamilyName": "Nascimento2", "visitorGroupName": GROUP,
                "remark": "1234", "faces": [{"faceData": "existing-photo-url"}],
            },
        }
        self.profile = {**self.booking["visitorInfo"]}
        self.person_group = "3"
        self.person_cards = [CARD]  # Earlier Person update already "confirmed".
        self.update_count = 0
        self.lost = False
        self.omit_profile_cards = False
        self.checkin = None

    def visitor_group_catalog(self):
        return [{"id": "1", "name": "Visitors"}, {"id": "3", "name": GROUP}]

    def find_registration_record(self, visitor_id, start, end, **kwargs):
        assert (visitor_id, start, end) == (VISITOR, START, END)
        return deepcopy(self.checkin)

    def post(self, path, payload):
        if path == PERSON_INFO:
            return {"data": {
                "personId": VISITOR, "orgIndexCode": self.person_group,
                "cards": [{"cardNo": card} for card in self.person_cards]}}
        if path == PERSON_UPDATE:
            assert payload["personId"] == VISITOR
            self.person_group = str(payload["orgIndexCode"])
            return {"code": "0"}
        if path == APPOINTMENTS_PATH:
            assert payload["appointCode"] == CODE
            return {"data": {"list": [deepcopy(self.booking)], "total": 1}}
        if path == DETAIL_PATH:
            info = deepcopy(self.profile)
            if self.omit_profile_cards:
                info.pop("cards", None)
            return {"data": {"VisitorInfo": info}}
        if path == UPDATE_BOOKING:
            self.update_count += 1
            data = payload["visitorInfoList"][0]["VisitorInfo"]
            assert payload["appointRecordId"] == APPT
            assert data["visitorId"] == VISITOR
            assert data["visitorGroupName"] == GROUP
            assert data["cards"] == [{"cardNo": CARD}]
            assert payload["appointStartTime"] == START
            assert payload["appointEndTime"] == END
            assert payload["accessInfo"] == self.booking["accessInfo"]
            assert "faces" not in data  # No photo loss or fake Base64.
            self.booking["appointID"] = "774452117501902850"
            self.booking["visitorInfo"].update(data)
            if not self.omit_profile_cards:
                self.profile.update(data)
            self.person_group = "1"  # HCP regression: Visitors after booking update.
            if self.lost:
                raise HikError("Resposta perdida")
            return {"data": {"appointRecordId": self.booking["appointID"]}}
        raise AssertionError("Endpoint inesperado: " + path)


def test_person_card_alone_is_not_proof_of_visitor_credential(tmp_path):
    api = Api()
    assert "cards" not in api.profile
    assert update_reserved_visitor_card(
        api, record(), CARD, GROUP, journal=tmp_path / "nfc.json")
    assert api.update_count == 1
    assert api.person_group == "3"
    assert api.booking["visitorInfo"]["cards"] == [{"cardNo": CARD}]
    assert api.profile["cards"] == [{"cardNo": CARD}]
    assert not (tmp_path / "nfc.json").exists()


def test_reservation_card_and_remark_update_one_safe_post(tmp_path):
    api = Api()
    assert update_reserved_visitor_card(
        api, record(), CARD, GROUP, remark="12340", journal=tmp_path / "nfc.json")
    assert api.update_count == 1
    assert api.profile["remark"] == "12340"
    assert api.booking["visitorInfo"]["remark"] == "12340"


def test_pending_uncertain_response_is_not_resent(tmp_path):
    api = Api()
    api.omit_profile_cards = True
    path = tmp_path / "nfc.json"
    with pytest.raises(EditorFieldError, match="não confirmou"):
        update_reserved_visitor_card(api, record(), CARD, GROUP, journal=path)
    assert api.update_count == 1
    assert path.exists()
    with pytest.raises(EditorFieldError, match="não foi confirmada"):
        update_reserved_visitor_card(api, record(), CARD, GROUP, journal=path)
    assert api.update_count == 1  # No second POST.


def test_lost_http_response_is_reconciled_from_visitor_and_booking(tmp_path):
    api = Api()
    api.lost = True
    assert update_reserved_visitor_card(
        api, record(), CARD, GROUP, journal=tmp_path / "nfc.json")
    assert api.update_count == 1


def test_duplicate_checkin_prevents_reservation_card_update(tmp_path):
    api = Api()
    api.checkin = {"recordId": "909"}
    with pytest.raises(EditorFieldError, match="check-in"):
        update_reserved_visitor_card(
            api, record(), CARD, GROUP, journal=tmp_path / "nfc.json")
    assert api.update_count == 0


def test_stale_reservation_id_prevents_update(tmp_path):
    api = Api()
    wrong = {**record(), "appointment_id": "other"}
    with pytest.raises(EditorFieldError, match="ID da reserva"):
        update_reserved_visitor_card(
            api, wrong, CARD, GROUP, journal=tmp_path / "nfc.json")
    assert api.update_count == 0


def test_visitor_card_checks_never_confuse_person_with_checkin():
    api = Api()
    registered = {**record(),
                  "entry_at": START, "exit_at": END,
                  "registration_record_id": "909"}
    api.checkin = {
        "recordId": "909", "visitorBaseInfo": {"visitorId": VISITOR},
        "visitorCardInfo": {"cardNumber": CARD}}
    with pytest.raises(EditorFieldError, match="não foi confirmada"):
        verify_checkedin_visitor_card(api, registered, CARD)
    api.profile["cards"] = [{"cardNo": CARD}]
    assert verify_checkedin_visitor_card(api, registered, CARD)
