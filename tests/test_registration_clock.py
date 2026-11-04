"""Regressões do horário digitado: sem HikCentral, Windows ou dados reais."""
from datetime import datetime
from types import SimpleNamespace
import inspect

import app
from core import TZ, default_settings, parse_local, registration_payload, Visitor
from visitor_history import save_registration


class Value:
    def __init__(self, value=""):
        self.value = value
    def get(self):
        return self.value
    def set(self, value):
        self.value = value


class Form:
    _set_current_ticket_period = app.ValeApp._set_current_ticket_period
    calculate = app.ValeApp.calculate

    def __init__(self):
        self.settings = default_settings()
        self.ticket = Value("1 dia")
        self.start, self.end = Value(), Value()


def moment(hour, minute):
    return datetime(2026, 11, 2, hour, minute, tzinfo=TZ)


def test_default_entry_is_ticket_opening_never_current_clock():
    for hour, minute in ((7, 30), (12, 57), (16, 44)):
        form = Form()
        form._set_current_ticket_period(moment(hour, minute))
        assert form.start.get() == "02/11/2026 09:00"
        assert form.end.get() == "02/11/2026 17:00"


def test_after_closing_default_entry_moves_to_next_opening():
    form = Form()
    form._set_current_ticket_period(moment(17, 0))
    assert form.start.get() == "03/11/2026 09:00"
    assert form.end.get() == "03/11/2026 17:00"


def test_default_uses_ticket_opening_when_customized():
    form = Form()
    form.ticket.set("1 dia")
    form.settings["tickets"]["1 dia"] = {
        "days": 1, "checkin": "10:30", "checkout": "18:00"}
    form._set_current_ticket_period(moment(16, 44))
    assert form.start.get() == "02/11/2026 10:30"
    assert form.end.get() == "02/11/2026 18:00"


def test_manual_entry_1644_is_preserved_but_never_generated_by_default():
    form = Form()
    form._set_current_ticket_period(moment(16, 44))
    form.start.set("04/11/2099 16:44")
    form.calculate()
    assert form.start.get() == "04/11/2099 16:44"
    assert form.end.get() == "04/11/2099 17:00"
    dt = parse_local(form.start.get())
    end = parse_local(form.end.get())
    payload = registration_payload(
        Visitor("Teste", "Local", "Day use", b"", "12345", dt, end))
    assert payload["visitStartTime"] == "2099-11-04T16:44:00-03:00"


def test_manual_outside_schedule_is_not_silently_rewritten():
    form = Form()
    form.ticket.set("2 dias")
    form.start.set("31/10/2099 20:45")
    form.calculate()
    assert form.start.get() == "31/10/2099 20:45"
    assert form.end.get() == "02/11/2099 17:00"


def test_registration_callback_never_changes_field_to_now():
    # Protege a UI de uma reintrodução do reset do relógio durante/após cadastrar.
    source = inspect.getsource(app.ValeApp.register)
    assert "_refresh_ticket_period_if_automatic" not in source
    assert "_set_current_ticket_period" not in source


def test_first_registration_timestamp_survives_history_updates(monkeypatch):
    import visitor_history
    saved = []
    monkeypatch.setattr(visitor_history, "_read", lambda base=None: saved[:])
    monkeypatch.setattr(visitor_history, "_write",
                        lambda records, base=None: saved.__setitem__(slice(None), records))

    record = {
        "visitor_id": "123", "door_ids": ["door1"], "access_names": ["VISITANTE_VALE"],
        "entry_at": "2026-11-02T12:57:00-03:00",
        "exit_at": "2026-11-04T17:00:00-03:00",
        "registered_at": "2026-11-02T13:12:07-03:00",
    }
    save_registration(record)
    assert saved[0]["registered_at"] == record["registered_at"]
    save_registration({**{k: v for k, v in record.items() if k != "registered_at"},
                       "state": "confirmed"})
    assert saved[0]["registered_at"] == record["registered_at"]
    assert saved[0]["entry_at"] == record["entry_at"]
