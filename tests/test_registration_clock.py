"""Relógio de atendimento: sem webcam, Windows, HikCentral ou dados reais."""
from datetime import datetime
from types import SimpleNamespace

import app
from core import TZ
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
    _refresh_ticket_period_if_automatic = app.ValeApp._refresh_ticket_period_if_automatic

    def __init__(self):
        self.start, self.end = Value(), Value()
        self.calls = 0

    def calculate(self):
        self.calls += 1
        self.end.set("Fim de " + self.start.get())


def moment(hour, minute):
    return datetime(2026, 11, 2, hour, minute, tzinfo=TZ)


def test_each_completed_registration_gets_new_current_time():
    form = Form()
    form._set_current_ticket_period(moment(12, 57))
    assert form.start.get() == "02/11/2026 12:57"
    assert form.end.get() == "Fim de 02/11/2026 12:57"

    form._set_current_ticket_period(moment(13, 12))
    assert form.start.get() == "02/11/2026 13:12"
    assert form.end.get() == "Fim de 02/11/2026 13:12"
    assert form.calls == 2


def test_operator_manually_edited_period_must_not_be_replaced(monkeypatch):
    form = Form()
    form._set_current_ticket_period(moment(12, 57))
    form.start.set("04/11/2026 10:30")
    form.end.set("05/11/2026 17:00")

    class Clock:
        @classmethod
        def now(cls, tz):
            return moment(13, 12)
    monkeypatch.setattr(app, "datetime", Clock)
    form._refresh_ticket_period_if_automatic()
    assert form.start.get() == "04/11/2026 10:30"
    assert form.end.get() == "05/11/2026 17:00"
    assert form.calls == 1


def test_untouched_period_is_refreshed_on_register(monkeypatch):
    form = Form()
    form._set_current_ticket_period(moment(12, 57))
    class Clock:
        @classmethod
        def now(cls, tz):
            return moment(13, 12)
    monkeypatch.setattr(app, "datetime", Clock)
    form._refresh_ticket_period_if_automatic()
    assert form.start.get() == "02/11/2026 13:12"
    assert form.end.get() == "Fim de 02/11/2026 13:12"


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
