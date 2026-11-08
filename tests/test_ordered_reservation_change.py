from datetime import datetime
from types import SimpleNamespace

import pytest

import ordered_reservation_change as ordered
from core import TZ, Visitor, registration_payload
from edit_errors import EditorFieldError
from remote_history import APPOINTMENTS_PATH


OLD_START = "2026-11-12T00:00:00-03:00"
OLD_END = "2026-11-13T23:59:59-03:00"
NEW_START = "2026-11-14T00:00:00-03:00"
NEW_END = "2026-11-15T23:59:59-03:00"
LEVEL = {"id": "7", "name": "VISITANTE_VALE"}


def _booking(aid, code, start, end):
    return {
        "appointID": aid, "appointCode": code, "appointStartTime": start,
        "appointEndTime": end, "appointStatus": 0,
        "visitorInfo": {"visitorId": "1865", "remark": "321"},
        "accessInfo": {"accessLevelList": {"accessLevel": [
            {"id": 7, "baseinfo": {"name": "VISITANTE_VALE"}}
        ]}},
    }


@pytest.fixture
def ordered_lab(monkeypatch, tmp_path):
    old = _booking("1000", "2502", OLD_START, OLD_END)
    bookings = {"2502": old}
    events = []
    history = []
    active = {"old": {"recordId": "check-old", "visitorStatus": "0",
                       "visitorBaseInfo": {"visitorId": "1865", "visitorGroup": "Hóspedes Vale",
                                           "visitStartTime": OLD_START, "visitEndTime": OLD_END},
                       "visitorCardInfo": {"cardNumber": "12345678"}}}
    new_row = None
    auto_checkin = False

    class Client:
        url = "https://hikcentral.test"

        def post(self, path, payload):
            if path == APPOINTMENTS_PATH:
                code = str(payload.get("appointCode") or "")
                rows = ([bookings[code]] if code in bookings else []) if code else list(bookings.values())
                return {"data": {"list": rows, "total": len(rows)}}
            if path == ordered.CHECKOUT:
                events.append("checkout")
                active["old"]["visitorStatus"] = "1"
                return {"code": "0"}
            if path == ordered.DELETE_BOOKING:
                events.append("delete")
                bookings.pop("2502", None)
                return {"code": "0"}
            raise AssertionError(f"Unexpected write: {path}")

        def find_registration_record(self, visitor_id, start, end, *, active_only=False, record_id=None):
            if record_id == "check-old":
                return active["old"]
            if new_row is not None and start == NEW_START and (not active_only or new_row["visitorStatus"] == "0"):
                return new_row
            return None

        def register_existing_visitor(self, visitor_id, payload, selected, *, appointment_id):
            events.append("checkin")
            assert visitor_id == "1865"
            assert appointment_id == "2000"
            nonlocal new_row
            new_row = {"recordId": "check-new", "visitorStatus": "0",
                       "visitorBaseInfo": {"visitorId": "1865", "visitorGroup": "Hóspedes Vale",
                                           "visitStartTime": NEW_START, "visitEndTime": NEW_END},
                       "visitorCardInfo": {"cardNumber": "12345678"}}
            return "check-new"

        def assign_access(self, visitor_id, level_id):
            events.append("assign")
            assert (visitor_id, level_id) == ("1865", "7")

        def apply(self, visitor_id, doors):
            events.append("apply")
            assert visitor_id == "1865" and doors == ["57", "64", "89"]

        def delivery_report(self, *_args, **_kwargs):
            raise AssertionError("Edição não deve esperar confirmação individual das catracas")

    client = Client()
    monkeypatch.setattr(ordered, "visitor_details", lambda *_args: {
        "VisitorInfo": {"visitorId": "1865", "visitorGroupName": "Hóspedes Vale"}
    })
    monkeypatch.setattr(ordered, "preserve_visitor_metadata", lambda payload, _details: payload)
    monkeypatch.setattr(ordered, "find_original_appointment", lambda *_args: old)
    monkeypatch.setattr(ordered, "_confirmed_edit", lambda *_args: "check-new")
    monkeypatch.setattr(ordered, "save_registration", history.append)

    visitor = Visitor("Teste", "Sobrenome", "Hóspedes Vale", b"", "12345678",
                      datetime.fromisoformat(NEW_START), datetime.fromisoformat(NEW_END), "321")
    payload = registration_payload(visitor, editing=True)
    record = {"visitor_id": "1865", "appointment_id": "1000",
              "registration_record_id": "check-old", "entry_at": OLD_START,
              "exit_at": OLD_END, "group": "Hóspedes Vale", "checkin_state": "check-in efetuado",
              "expect_card": True}

    def prepare(original, _payload):
        events.append("preflight")
        assert original["appointID"] == "1000"

        def create():
            nonlocal new_row
            events.append("create")
            bookings["5054"] = _booking("2000", "5054", NEW_START, NEW_END)
            if auto_checkin:
                new_row = {"recordId": "check-auto", "visitorStatus": "0",
                           "visitorBaseInfo": {"visitorId": "1865", "visitorGroup": "Hóspedes Vale",
                                               "visitStartTime": NEW_START, "visitEndTime": NEW_END},
                           "visitorCardInfo": {"cardNumber": "12345678"}}
                return {"visitor_id": "1865", "appointment_id": "2000", "code": "5054"}
            return {"visitor_id": "1865", "appointment_id": "2000", "code": "5054"}
        return create

    def run(*, prepare_creation=prepare, journal=None):
        return ordered.change_reservation_dates(
            client, record, payload, [dict(LEVEL)], ["57", "64", "89"], visitor,
            "Hóspedes Vale", ["VISITANTE_VALE"], prepare_creation=prepare_creation,
            journal=journal or tmp_path / "ordered.json")

    def set_auto(value):
        nonlocal auto_checkin
        auto_checkin = bool(value)

    return SimpleNamespace(client=client, run=run, events=events, bookings=bookings,
                           history=history, active=active, record=record,
                           set_auto=set_auto)


def test_date_change_follows_checkout_delete_create_checkin_order(ordered_lab):
    result = ordered_lab.run()
    assert ordered_lab.events == ["preflight", "checkout", "delete", "create",
                                  "checkin", "assign", "apply"]
    assert "2502" not in ordered_lab.bookings
    assert ordered_lab.bookings["5054"]["appointID"] == "2000"
    assert result["rebooking_completed"] is True
    assert result["automatic_checkin"] is False
    assert result["confirmed_appointment_id"] == "2000"
    assert result["confirmed_checkin_id"] == "check-new"
    assert result["state"] == "submitted"
    assert result["operation_completed"] is True
    assert ordered_lab.history[0]["appointment_id"] == "2000"
    assert ordered_lab.history[0]["state"] == "submitted"


def test_missing_web_client_preflight_stops_before_checkout(ordered_lab):
    def denied(*_args):
        raise EditorFieldError({"appointment": "Web Client sem permissão para criar reserva."})

    with pytest.raises(EditorFieldError, match="sem permissão"):
        ordered_lab.run(prepare_creation=denied)
    assert ordered_lab.events == []
    assert "2502" in ordered_lab.bookings
    assert ordered_lab.active["old"]["visitorStatus"] == "0"


def test_skips_manual_checkin_when_hikcentral_already_created_it(ordered_lab):
    ordered_lab.set_auto(True)
    result = ordered_lab.run()
    assert ordered_lab.events == ["preflight", "checkout", "delete", "create",
                                  "assign", "apply"]
    assert result["automatic_checkin"] is True
    assert result["confirmed_checkin_id"] == "check-auto"


def test_recovers_booking_created_before_web_client_timeout_without_duplicate(ordered_lab):
    def create_then_timeout():
        ordered_lab.events.append("create")
        ordered_lab.bookings["5054"] = _booking("2000", "5054", NEW_START, NEW_END)
        raise TimeoutError("response timed out after commit")

    result = ordered_lab.run(prepare_creation=lambda *_args: create_then_timeout)
    assert ordered_lab.events.count("create") == 1
    assert ordered_lab.events.count("checkin") == 1
    assert len(ordered_lab.bookings) == 1
    assert result["confirmed_appointment_id"] == "2000"
