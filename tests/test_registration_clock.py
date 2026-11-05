"""Regressões do período do visitante: apenas datas na recepção."""
from datetime import datetime
import inspect
import pytest

import app
from core import (TZ, default_settings, parse_visit_date, registration_payload,
                  visitor_period_from_dates, Visitor)
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


@pytest.mark.parametrize("hour, minute", [(0, 12), (7, 30), (12, 57), (16, 44), (22, 59)])
def test_ui_default_has_only_calendar_date_regardless_of_clock(hour, minute):
    form = Form()
    form._set_current_ticket_period(moment(hour, minute))
    assert form.start.get() == "02/11/2026"
    assert form.end.get() == "02/11/2026"
    assert ":" not in form.start.get()
    assert ":" not in form.end.get()


def test_two_days_cross_month_without_changing_start():
    form = Form()
    form.ticket.set("2 dias")
    form.start.set("31/10/2099")
    form.calculate()
    assert form.start.get() == "31/10/2099"
    assert form.end.get() == "01/11/2099"


def test_ticket_operational_hours_are_not_used_in_validity():
    form = Form()
    form.settings["tickets"]["1 dia"] = {
        "days": 1, "checkin": "10:30", "checkout": "18:00"}
    form._set_current_ticket_period(moment(16, 44))
    assert form.start.get() == "02/11/2026"
    assert form.end.get() == "02/11/2026"
    entry, exit_ = visitor_period_from_dates(
        form.start.get(), form.end.get(), form.settings,
        form.settings["tickets"]["1 dia"])
    assert (entry.strftime("%H:%M:%S"), exit_.strftime("%H:%M:%S")) == (
        "00:00:00", "23:59:59")


def test_1644_cannot_be_entered_into_date_fields():
    form = Form()
    form._set_current_ticket_period(moment(16, 44))
    form.start.set("04/11/2099 16:44")
    with pytest.raises(ValueError, match="apenas a data"):
        visitor_period_from_dates(
            form.start.get(), "04/11/2099", form.settings,
            form.settings["tickets"]["1 dia"])
    form.start.set("04/11/2099")
    form.calculate()
    entry, end = visitor_period_from_dates(
        form.start.get(), form.end.get(), form.settings,
        form.settings["tickets"]["1 dia"])
    payload = registration_payload(
        Visitor("Teste", "Local", "Day use", b"", "12345", entry, end))
    assert payload["visitStartTime"] == "2099-11-04T00:00:00-03:00"
    assert payload["visitEndTime"] == "2099-11-04T23:59:59-03:00"


def test_registration_never_refreshes_form_from_clock():
    source = inspect.getsource(app.ValeApp.register)
    assert "_refresh_ticket_period_if_automatic" not in source
    assert "_set_current_ticket_period" not in source
    assert "visitor_period_from_dates(" in source

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
