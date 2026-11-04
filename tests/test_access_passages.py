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
    assert "eventType" not in sent  # personId permite buscar vários métodos.
    assert result["passage_diagnostics"]["successful_access"] == 4
    assert result["passage_diagnostics"]["without_direction"] == 0
    assert result["first_access_at"] == "2026-11-02T09:35:00-03:00"
    assert result["last_access_at"] == "2026-11-03T10:58:20-03:00"


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
    result = fetch_visitor_passages(client, visitor(), now=now())
    assert result["first_entry_at"] == ""
    assert result["last_exit_at"] == ""
    assert result["passage_diagnostics"]["successful_access"] == 0


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
    result = fetch_visitor_passages(client, rec, now=now())
    assert result["first_entry_at"] == ""
    assert result["last_exit_at"] == ""
    assert result["passage_diagnostics"]["events_returned"] == 0
    assert client.calls == []


def test_duplicate_events_do_not_change_extremes():
    same = ev(196893, 1, "2026-11-02T10:05:00-03:00")
    same["eventId"] = "abc"
    client = MockACS({1: {"total": 2, "list": [same, dict(same)]}})
    result = fetch_visitor_passages(client, visitor(), now=now())
    assert result["first_entry_at"] == "2026-11-02T10:05:00-03:00"
    assert result["last_exit_at"] == ""


def test_visitor_access_record_undirected_authentications_are_reported_not_guessed():
    """A página pode mostrar 1ª/última autenticação sem saber se era entrada/saída."""
    client = MockACS({1: {"total": 2, "list": [
        ev(196893, 0, "2026-11-02T09:34:07-03:00", reader="Catraca Vale"),
        ev(198914, 0, "2026-11-03T10:01:08-03:00", reader="Catraca Vale"),
    ]}})
    result = fetch_visitor_passages(client, visitor(), now=now())
    assert result["first_access_at"] == "2026-11-02T09:34:07-03:00"
    assert result["last_access_at"] == "2026-11-03T10:01:08-03:00"
    # Não chamar o último acesso de SAÍDA se o leitor não indica sentido.
    assert result["first_entry_at"] == ""
    assert result["last_exit_at"] == ""
    assert result["passage_diagnostics"]["successful_access"] == 2
    assert result["passage_diagnostics"]["without_direction"] == 2


def test_visitor_access_record_fallback_by_name_and_card_requires_exact_identity():
    """Servidor antigo ignora/falha filtro por ID; nome isolado não basta."""
    class Legacy:
        calls = []
        def post(self, path, payload):
            assert path == DOOR_EVENTS_PATH
            self.calls.append(dict(payload))
            if "personId" in payload:
                return {"data": {"total": 0, "list": []}}
            return {"data": {"total": 3, "list": [
                {**ev(196893, 1, "2026-11-02T09:11:00-03:00", person="other"),
                 "cardNo": "ABC1", "personName": "Maria Teste"},
                {**ev(198914, 2, "2026-11-02T10:30:00-03:00", person="other"),
                 "cardNo": "ABC1", "personName": "Outra Pessoa"},
                {**ev(198914, 2, "2026-11-02T11:30:00-03:00", person="another"),
                 "cardNo": "ABC1", "personName": "Maria Teste"},
            ]}}
    rec = visitor()
    rec.update(first="Maria", last="Teste", card_number="ABC1")
    client = Legacy()
    result = fetch_visitor_passages(client, rec, now=now())
    assert result["first_entry_at"] == "2026-11-02T09:11:00-03:00"
    assert result["last_exit_at"] == "2026-11-02T11:30:00-03:00"
    assert result["passage_diagnostics"]["by_name_fallback"] == 1
    assert result["passage_diagnostics"]["successful_access"] == 2
    assert client.calls[0]["personId"] == "812"
    assert any(p.get("personName") == "Maria Teste" for p in client.calls)


def test_homonym_without_card_or_id_cannot_be_assigned_to_visitor():
    class Legacy:
        def post(self, path, payload):
            if "personId" in payload:
                return {"data": {"total": 0, "list": []}}
            return {"data": {"total": 1, "list": [
                {**ev(196893, 1, "2026-11-02T10:00:00-03:00", person="not-812"),
                 "personName": "Maria Teste"}
            ]}}
    rec = visitor()
    rec.update(first="Maria", last="Teste", card_number="")
    result = fetch_visitor_passages(Legacy(), rec, now=now())
    assert result["first_entry_at"] == ""
    assert result["last_exit_at"] == ""
    assert result["passage_diagnostics"]["other_visitors"] == 1


def test_incomplete_pages_never_fall_back_to_partial_results(monkeypatch):
    import access_passages
    monkeypatch.setattr(access_passages, "PAGE_SIZE", 1)
    monkeypatch.setattr(access_passages, "MAX_PAGES_PER_PERIOD", 1)
    class Legacy:
        called = 0
        def post(self, path, payload):
            self.called += 1
            return {"data": {"total": 3, "list": [
                ev(196893, 1, "2026-11-02T10:00:00-03:00")
            ]}}
    client = Legacy()
    with pytest.raises(HikError, match="mais páginas"):
        fetch_visitor_passages(client, visitor(), now=now())
    assert client.called == 1


def test_acs_eventtype_required_fallback_finds_real_face_entry_and_exit():
    """HikCentral pode rejeitar consulta sem eventType; nunca inventar passagem."""
    class TypedOnly:
        def __init__(self):
            self.calls = []
        def post(self, path, payload):
            assert path == DOOR_EVENTS_PATH
            self.calls.append(dict(payload))
            if "eventType" not in payload:
                raise HikError("eventType obrigatório no OpenAPI")
            if payload["eventType"] == 196893:
                return {"data": {"total": 2, "list": [
                    ev(196893, 1, "2026-11-02T10:05:00-03:00"),
                    ev(196893, 2, "2026-11-03T10:42:00-03:00"),
                ]}}
            return {"data": {"total": 0, "list": []}}
    cli = TypedOnly()
    result = fetch_visitor_passages(cli, visitor(), now=now())
    assert result["first_entry_at"] == "2026-11-02T10:05:00-03:00"
    assert result["last_exit_at"] == "2026-11-03T10:42:00-03:00"
    assert result["passage_diagnostics"]["typed_fallback"] >= 1
    assert result["passage_diagnostics"]["unfiltered_errors"] >= 1
    assert any(p.get("eventType") == 196893 for p in cli.calls)


def test_acs_typed_query_does_not_count_denied_or_foreign_person():
    class TypedOnly:
        def post(self, path, payload):
            if "eventType" not in payload:
                raise HikError("eventType obrigatório")
            if payload["eventType"] == 196893:
                return {"data": {"total": 2, "list": [
                    ev(197151, 1, "2026-11-02T10:05:00-03:00"),
                    ev(196893, 2, "2026-11-03T10:42:00-03:00", person="999"),
                ]}}
            return {"data": {"total": 0, "list": []}}
    result = fetch_visitor_passages(TypedOnly(), visitor(), now=now())
    assert not result["first_entry_at"] and not result["last_exit_at"]
    assert result["passage_diagnostics"]["successful_access"] == 0


def test_known_approved_door_ids_are_sent_to_acs_query():
    rec = visitor()
    rec["door_ids"] = ["door-in", "door-out"]
    fake = MockACS({1: {"total": 1, "list": [
        {**ev(196893, 1, "2026-11-02T10:05:00-03:00"),
         "doorIndexCode": "door-in"},
    ]}})
    result = fetch_visitor_passages(fake, rec, now=now())
    assert fake.calls[0]["doorIndexCodes"] == ["door-in", "door-out"]
    assert result["first_entry_at"] == "2026-11-02T10:05:00-03:00"


@pytest.mark.parametrize("label,expected", [
    ("CATRACA ENTRADA 1", "entry"),
    ("Recepção / Entrada Principal", "entry"),
    ("PORTARIA SAÍDA 2", "exit"),
    ("LEITOR SAI 1", "exit"),
    ("ENTRADA E SAÍDA", None),
    ("Cardreader 01", None),
    ("PONTO DE ACESSO", None),
])
def test_reader_direction_matches_explicit_words_anywhere_not_just_prefix(
        label, expected):
    from access_passages import _direction
    assert _direction({"readerName": label, "checkInAndOutType": 0}) == expected


def test_reader_index_lookup_when_event_name_missing():
    from access_passages import _direction
    assert _direction(
        {"readerIndexCode": "reader-entrance", "checkInAndOutType": 0},
        {"reader-entrance": "entry"}) == "entry"
    assert _direction(
        {"readerIndexCode": "reader-exit"},
        {"reader-exit": "exit"}) == "exit"
    assert _direction({"readerIndexCode": "unknown"}, {}) is None
    # Explicitly contradictory name and mapping is not safe to infer.
    assert _direction(
        {"readerIndexCode": "reader-entrance", "readerName": "SAIDA"},
        {"reader-entrance": "entry"}) is None
    # Documented checkInAndOutType overrides missing/ambiguous reader names.
    assert _direction({"checkInAndOutType": 1, "readerName": "Cardreader"}) == "entry"


def test_load_reader_directions_queries_metadata_once_per_door():
    from access_passages import load_reader_directions, READER_SEARCH_PATH
    class MockReaders:
        def __init__(self):
            self.calls = []
        def post(self, path, payload):
            assert path == READER_SEARCH_PATH
            self.calls.append(payload["doorIndexCode"])
            if payload["doorIndexCode"] == "door1":
                return {"data": {"total": 2, "list": [
                    {"indexCode": "readerA", "name": "Catraca ENTRADA 1"},
                    {"indexCode": "readerG", "name": "Cardreader 02"},
                ]}}
            return {"data": {"total": 1, "list": [
                {"indexCode": "readerB", "name": "CATRACA SAIDA 1"},
            ]}}
    fake = MockReaders()
    resolved = load_reader_directions(fake, ["door1", "door2", "door1"])
    assert fake.calls == ["door1", "door2"]
    assert resolved == {"readerA": "entry", "readerB": "exit"}


def test_reader_metadata_recovers_first_entry_without_attendance_direction():
    from access_passages import load_reader_directions, READER_SEARCH_PATH
    class ReaderACS:
        def post(self, path, payload):
            if path == READER_SEARCH_PATH:
                return {"data": {"list": [
                    {"indexCode": "reader-in-01",
                     "name": "CATRACA RECEPÇÃO ENTRADA"},
                    {"indexCode": "reader-out-01", "name": "CATRACA SAIDA 1"},
                ]}}
            assert path == DOOR_EVENTS_PATH
            return {"data": {"total": 2, "list": [
                {**ev(196893, 0, "2026-11-02T09:17:20-03:00"),
                 "readerIndexCode": "reader-in-01"},
                {**ev(196893, 0, "2026-11-03T10:17:20-03:00"),
                 "readerIndexCode": "reader-out-01"},
            ]}}
    api = ReaderACS()
    readers = load_reader_directions(api, ["door1"])
    result = fetch_visitor_passages(
        api, visitor(), now=now(), reader_directions=readers)
    assert result["first_entry_at"] == "2026-11-02T09:17:20-03:00"
    assert result["last_exit_at"] == "2026-11-03T10:17:20-03:00"
    assert result["passage_diagnostics"]["without_direction"] == 0
    assert result["passage_diagnostics"]["direction_from_reader_id"] == 2


def test_generic_reader_names_never_fabricate_first_entry():
    from access_passages import load_reader_directions, READER_SEARCH_PATH
    class Generic:
        def post(self, path, payload):
            if path == READER_SEARCH_PATH:
                return {"data": {"list": [
                    {"indexCode": "reader-01", "name": "Cardreader 01"},
                ]}}
            return {"data": {"total": 1, "list": [
                {**ev(196893, 0, "2026-11-02T09:11:00-03:00"),
                 "readerIndexCode": "reader-01"},
            ]}}
    api = Generic()
    result = fetch_visitor_passages(
        api, visitor(), now=now(),
        reader_directions=load_reader_directions(api, ["door1"]))
    assert not result["first_entry_at"]
    assert result["passage_diagnostics"]["without_direction"] == 1
    assert result["passage_diagnostics"]["unknown_reader_codes"] == ["reader-01"]
