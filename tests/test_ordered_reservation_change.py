from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

import ordered_reservation_change as ordered
from core import Visitor, registration_payload
from hikcentral import HikError
from remote_history import APPOINTMENTS_PATH


OLD_START = "2099-11-12T00:00:00-03:00"
OLD_END = "2099-11-13T23:59:59-03:00"
NEW_START = "2099-11-14T00:00:00-03:00"
NEW_END = "2099-11-15T23:59:59-03:00"
LEVEL = {"id": "7", "name": "VISITANTE_VALE"}


def _booking(aid, code, start, end):
    return {
        "appointID": aid, "appointCode": code, "appointStartTime": start,
        "appointEndTime": end, "appointStatus": 3,
        "visitReasonType": 4, "visitReasonDetail": "Hospedagem",
        "visitorInfo": {"visitorId": "1865", "remark": "321",
                        "visitorGroupName": "Hóspedes Vale",
                        "visitorGivenName": "Teste", "visitorFamilyName": "Sobrenome",
                        "accessInfo": {"qrCodeValidNum": 12}},
        "accessInfo": {"accessLevelList": {"accessLevel": [
            {"id": 7, "baseinfo": {"name": "VISITANTE_VALE"}}
        ]}},
    }


@pytest.fixture
def ordered_lab(monkeypatch, tmp_path):
    original = _booking("1000", "2502", OLD_START, OLD_END)
    bookings = {"2502": original}
    events = []
    history = []
    journal_path = tmp_path / "ordered.json"
    old_visit = {"recordId": "check-old", "visitorStatus": "0",
                 "visitorBaseInfo": {"visitorId": "1865", "visitorGroup": "Hóspedes Vale",
                                     "visitStartTime": OLD_START, "visitEndTime": OLD_END},
                 "visitorCardInfo": {"cardNumber": "12345678"}}
    active_rows = {}
    auto_checkin = False
    update_commits = True
    update_reply_timeout = False
    keep_old_booking = False
    writes = []

    class Client:
        url = "https://hikcentral.test"

        @staticmethod
        def access_info(levels):
            from hikcentral import HikCentral
            return HikCentral.access_info(levels)

        def post(self, path, payload):
            nonlocal update_commits, update_reply_timeout, keep_old_booking
            if path == APPOINTMENTS_PATH:
                code = str(payload.get("appointCode") or "")
                rows = ([deepcopy(bookings[code])] if code in bookings else []) if code else list(
                    deepcopy(list(bookings.values())))
                return {"data": {"list": rows, "total": len(rows)}}
            if path == ordered.CHECKOUT:
                events.append("checkout")
                old_visit["visitorStatus"] = "1"
                return {"code": "0"}
            if path == ordered.UPDATE_BOOKING:
                events.append("reservation_update")
                writes.append(deepcopy(payload))
                assert payload["appointRecordId"] == "1000"
                assert payload["appointStartTime"] == NEW_START
                assert payload["appointEndTime"] == NEW_END
                row = deepcopy(bookings["2502"])
                row.update(appointID="2000", appointStartTime=NEW_START, appointEndTime=NEW_END)
                visitor_info = row["visitorInfo"]
                visitor_info.update(payload["visitorInfoList"][0]["VisitorInfo"])
                if update_commits:
                    bookings["2502"] = row
                    if keep_old_booking:
                        bookings["old-copy"] = deepcopy(original)
                    if auto_checkin:
                        active_rows[NEW_START] = {
                            "recordId": "check-auto", "visitorStatus": "0",
                            "visitorBaseInfo": {"visitorId": "1865", "visitorGroup": "Hóspedes Vale",
                                                "visitStartTime": NEW_START, "visitEndTime": NEW_END},
                            "visitorCardInfo": {"cardNumber": "12345678"}}
                if update_reply_timeout:
                    raise TimeoutError("response timed out after commit")
                return {"code": "0", "data": {"appointRecordId": "2000"}}
            raise AssertionError(f"Unexpected write: {path}")

        def find_registration_record(self, visitor_id, start, end, *, active_only=False, record_id=None):
            if record_id == "check-old":
                return old_visit
            row = active_rows.get(start)
            if row and (not active_only or row["visitorStatus"] == "0"):
                return row
            return None

        def register_existing_visitor(self, visitor_id, payload, selected, *, appointment_id):
            events.append("checkin")
            assert visitor_id == "1865"
            assert appointment_id == "2000"
            row = {"recordId": "check-new", "visitorStatus": "0",
                   "visitorBaseInfo": {"visitorId": "1865", "visitorGroup": "Hóspedes Vale",
                                       "visitStartTime": NEW_START, "visitEndTime": NEW_END},
                   "visitorCardInfo": {"cardNumber": "12345678"}}
            active_rows[NEW_START] = row
            return "check-new"

        def assign_access(self, visitor_id, level_id):
            events.append("assign")
            assert (visitor_id, level_id) == ("1865", "7")

        def apply(self, visitor_id, doors):
            events.append("apply")
            assert visitor_id == "1865" and doors == ["57", "64", "89"]

        def delivery_report(self, *_args, **_kwargs):
            raise AssertionError("A edição não deve esperar confirmação individual das catracas")

    client = Client()
    monkeypatch.setattr(ordered, "visitor_details", lambda *_args: {
        "VisitorInfo": {"visitorId": "1865", "visitorGroupName": "Hóspedes Vale"}
    })
    monkeypatch.setattr(ordered, "find_original_appointment", lambda *_args: deepcopy(original))
    monkeypatch.setattr(ordered, "_confirmed_edit", lambda *_args: "check-new")
    monkeypatch.setattr(ordered, "save_registration", history.append)
    monkeypatch.setattr(ordered, "fetch_visitor_photo", lambda *_args: b"")

    visitor = Visitor("Teste", "Sobrenome", "Hóspedes Vale", b"", "12345678",
                      datetime.fromisoformat(NEW_START), datetime.fromisoformat(NEW_END), "321")
    payload = registration_payload(visitor, editing=True)
    record = {"visitor_id": "1865", "appointment_id": "1000",
              "registration_record_id": "check-old", "entry_at": OLD_START,
              "exit_at": OLD_END, "group": "Hóspedes Vale", "checkin_state": "check-in efetuado",
              "expect_card": True}

    def run(*, journal=None):
        return ordered.change_reservation_dates(
            client, record, payload, [dict(LEVEL)], ["57", "64", "89"], visitor,
            "Hóspedes Vale", ["VISITANTE_VALE"], journal=journal or journal_path,
            profile_update=lambda: events.append("profile_update"))

    def _set_auto(value):
        nonlocal auto_checkin
        auto_checkin = bool(value)

    def _set_commit(value):
        nonlocal update_commits
        update_commits = bool(value)

    def _set_timeout(value):
        nonlocal update_reply_timeout
        update_reply_timeout = bool(value)

    def _set_keep_old(value):
        nonlocal keep_old_booking
        keep_old_booking = bool(value)

    return SimpleNamespace(client=client, run=run, events=events, bookings=bookings,
                           history=history, old_visit=old_visit, active_rows=active_rows,
                           writes=writes, journal=journal_path, set_auto=_set_auto,
                           set_commit=_set_commit, set_timeout=_set_timeout,
                           set_keep_old=_set_keep_old)


def test_date_change_checks_out_then_updates_original_reservation_through_openapi(ordered_lab):
    result = ordered_lab.run()
    assert ordered_lab.events == ["checkout", "profile_update", "reservation_update", "checkin", "assign", "apply"]
    assert ordered_lab.bookings["2502"]["appointID"] == "2000"
    assert ordered_lab.bookings["2502"]["visitorInfo"]["visitorId"] == "1865"
    assert result["rebooking_completed"] is True
    assert result["automatic_checkin"] is False
    assert result["confirmed_appointment_id"] == "2000"
    assert result["confirmed_checkin_id"] == "check-new"
    assert result["state"] == "submitted"
    assert result["operation_completed"] is True
    assert ordered_lab.history[0]["appointment_id"] == "2000"
    assert ordered_lab.history[0]["state"] == "submitted"


def test_reservation_update_contains_complete_profile_but_never_card_or_face(ordered_lab):
    ordered_lab.run()
    body, = ordered_lab.writes
    sent = body["visitorInfoList"][0]["VisitorInfo"]
    assert body["appointRecordId"] == "1000"
    assert body["appointStartTime"] == NEW_START
    assert body["appointEndTime"] == NEW_END
    assert sent["visitorId"] == "1865"
    assert sent["visitorGivenName"] == "Teste"
    assert sent["visitorFamilyName"] == "Sobrenome"
    assert sent["remark"] == "321"
    assert "cards" not in sent and "faces" not in sent


def test_does_not_create_a_second_visitor_or_call_browser(ordered_lab):
    ordered_lab.run()
    assert "create" not in ordered_lab.events
    assert "delete" not in ordered_lab.events
    assert not hasattr(ordered_lab.client, "create_appointment_for_existing_visitor")


def test_openapi_update_timeout_after_commit_is_reconciled_without_duplicate(ordered_lab):
    ordered_lab.set_timeout(True)
    result = ordered_lab.run()
    assert ordered_lab.events.count("reservation_update") == 1
    assert ordered_lab.events.count("checkin") == 1
    assert len(ordered_lab.bookings) == 1
    assert result["confirmed_appointment_id"] == "2000"


def test_retries_update_only_after_readback_proves_original_is_still_intact(ordered_lab):
    ordered_lab.set_commit(False)
    with pytest.raises(HikError, match="reserva original foi preservada"):
        ordered_lab.run()
    assert ordered_lab.journal.exists()
    assert ordered_lab.events == ["checkout", "profile_update", "reservation_update"]

    ordered_lab.set_commit(True)
    result = ordered_lab.run()
    assert ordered_lab.events.count("reservation_update") == 2
    assert ordered_lab.events.count("checkin") == 1
    assert result["confirmed_appointment_id"] == "2000"


def test_skips_manual_checkin_when_hikcentral_already_created_it(ordered_lab):
    ordered_lab.set_auto(True)
    result = ordered_lab.run()
    assert ordered_lab.events == ["checkout", "profile_update", "reservation_update", "assign", "apply"]
    assert result["automatic_checkin"] is True
    assert result["confirmed_checkin_id"] == "check-auto"


def test_refuses_to_continue_if_api_leaves_old_and_new_reservations(ordered_lab):
    ordered_lab.set_keep_old(True)
    with pytest.raises(HikError, match="manteve a reserva antiga"):
        ordered_lab.run()
    assert "checkin" not in ordered_lab.events
