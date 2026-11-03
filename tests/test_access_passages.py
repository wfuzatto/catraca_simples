"""Testes do histórico ACS: nunca usam credenciais ou catracas reais."""
from datetime import datetime, timedelta
import pytest

from access_passages import (
    DOOR_EVENTS_PATH, GRANTED_TYPES, _direction, fetch_visitor_passages,
)
from core import TZ
from hikcentral import HikError


def visitor():
    return {
        "visitor_id": "812", "first": "Maria", "last": "Teste",
        "entry_at": "2026-11-02T09:00:00-03:00",
        "exit_at": "2026-11-04T17:00:00-03:00",
    }


def now():
    return datetime(2026, 11, 3, 11, tzinfo=TZ)


class MockACS:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
    def post(self, path, payload):
        assert path == DOOR_EVENTS_PATH
        self.calls.append(payload)
        page = payload["pageNo"]
        return {"data": self.pages.get(page, {"total": 0, "list": []})}


def ev(typ, direction, at, person="812", reader=""):
    data = {
        "eventType": typ, "personId": person,
        "eventTime": at, "checkInAndOutType": direction,
    }
    if reader:
        data["readerName"] = reader
    return data


def test_first_entry_and_last_exit_only_from_granted_events():
    pages = {1: {"total": 7, "list": [
        ev(196893, 1, "2026-11-02T09:35:00-03:00"),
        ev(197151, 2, "2026-11-02T10:10:00-03:00"),  # Rosto NEGADO
        ev(198914, 2, "2026-11-02T10:45:00-03:00"),
        ev(196893, 1, "2026-11-03T09:10:00-03:00"),
        ev(110025, 2, "2026-11-03T10:58:20-03:00"),
        ev(198914, 1, "2026-11-02T09:00:00-03:00", person="999"),
        ev(196893, 2, "2026-11-04T12:00:00-03:00"),  # AINDA futuro
    ]}}
    client = MockACS(pages)
    result = fetch_visitor_passages(client, visitor(), now=now())
    assert result["first_entry_at"] == "2026-11-02T09:35:00-03:00"
    assert result["last_exit_at"] == "2026-11-03T10:58:20-03:00"
    assert len(client.calls) == 1
    sent = client.calls[0]
    assert sent["personId"] == "812"
    assert sent["personType"] == 1
    assert sent["doorIndexCodes"] == []
    assert "eventType" not in sent  # Permitir QR, NFC e face simultaneamente.


def test_last_exit_is_not_visitor_administrative_checkout():
    client = MockACS({1: {"total": 1, "list": [
        ev(196893, 1, "2026-11-02T14:00:00-03:00")
    ]}})
    result = fetch_visitor_passages(client, visitor(), now=now())
    assert result["first_entry_at"]
    assert result["last_exit_at"] == ""


@pytest.mark.parametrize("field,value,expected", [
    ("checkInAndOutType", 1, "entry"),
    ("checkInAndOutType", 2, "exit"),
    ("readerName", "ENT 1", "entry"),
    ("readerName", "SAI 1", "exit"),
    ("doorName", "Entrada Vale", "entry"),
    ("doorName", "Saída Serra", "exit"),
    ("doorName", "Corredor 1", None),
])
def test_direction_requires_documented_attendance_or_explicit_reader(field, value, expected):
    record = {} if field != "checkInAndOutType" else {"readerName": "Corredor"}
    record[field] = value
    assert _direction(record) == expected


def test_does_not_classify_access_denied_even_if_direction_known():
    assert 197151 not in GRANTED_TYPES
    client = MockACS({1: {"total": 1, "list": [
        ev(197151, 1, "2026-11-02T10:00:00-03:00")
    ]}})
    assert fetch_visitor_passages(client, visitor(), now=now()) == {
        "first_entry_at": "", "last_exit_at": "",
    }


def test_pagination_includes_last_exit_on_final_page(monkeypatch):
    import access_passages
    monkeypatch.setattr(access_passages, "PAGE_SIZE", 2)
    client = MockACS({
        1: {"total": 3, "list": [
            ev(196893, 1, "2026-11-02T10:00:00-03:00"),
            ev(198914, 2, "2026-11-02T13:00:00-03:00"),
        ]},
        2: {"total": 3, "list": [
            ev(198914, 2, "2026-11-03T10:00:00-03:00"),
        ]},
    })
    result = fetch_visitor_passages(client, visitor(), now=now())
    assert len(client.calls) == 2
    assert result["last_exit_at"] == "2026-11-03T10:00:00-03:00"


def test_api_errors_and_long_results_do_not_produce_fake_times(monkeypatch):
    import access_passages
    class Bad:
        def post(self, *args):
            return {"data": {"unexpected": []}}
    with pytest.raises(HikError, match="incompatíveis"):
        fetch_visitor_passages(Bad(), visitor(), now=now())

    monkeypatch.setattr(access_passages, "PAGE_SIZE", 1)
    monkeypatch.setattr(access_passages, "MAX_PAGES_PER_PERIOD", 1)
    client = MockACS({1: {"total": 3, "list": [
        ev(196893, 1, "2026-11-02T10:00:00-03:00")
    ]}})
    with pytest.raises(HikError, match="mais páginas"):
        fetch_visitor_passages(client, visitor(), now=now())


def test_no_future_events_and_excludes_other_visit_of_same_person():
    rec = visitor()
    rec["entry_at"] = "2026-11-03T12:00:00-03:00"
    client = MockACS({})
    assert fetch_visitor_passages(client, rec, now=now()) == {
        "first_entry_at": "", "last_exit_at": ""}
    assert client.calls == []


def test_duplicate_events_do_not_change_extremes():
    same = ev(196893, 1, "2026-11-02T10:05:00-03:00")
    same["eventId"] = "abc"
    client = MockACS({1: {"total": 2, "list": [same, dict(same)]}})
    result = fetch_visitor_passages(client, visitor(), now=now())
    assert result["first_entry_at"] == "2026-11-02T10:05:00-03:00"
    assert result["last_exit_at"] == ""
