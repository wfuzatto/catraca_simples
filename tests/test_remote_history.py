"""Consultas remotas simuladas: não exigem servidor, webcam nem cartão real."""
import base64
import io
from datetime import datetime
from PIL import Image
import pytest
from remote_history import (
    CHECKINS_PATH, PHOTO_PATH, DETAIL_PATH, search_window,
    fetch_checkins, fetch_combined_history, fetch_visitor_photo, normalize_checkin,
    decode_photo_response, visitor_details,
)
from hikcentral import HikError


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.calls = []
    def post(self, path, payload, **options):
        self.calls.append((path, payload, options))
        return self.data


def example_record(visitor="812", record="40", name="Maria Alves"):
    return {
        "recordId": record,
        "visitorStatus": "0",
        "visitorBaseInfo": {
            "visitorId": visitor, "fullName": name,
            "visitStartTime": "2026-11-02T12:57:00-03:00",
            "visitEndTime": "2026-11-04T17:00:00-03:00",
            "visitorGroup": "Hóspedes Vale", "remark": "Apto 18",
            "smallPhotoUrl": "opaque-photo-id",
        },
    }


def test_date_filter_includes_last_day_and_rejects_long_queries():
    start, end = search_window("01/11/2026", "02/11/2026")
    assert start == "2026-11-01T00:00:00-03:00"
    assert end == "2026-11-03T00:00:00-03:00"
    with pytest.raises(ValueError, match="32 dias"):
        search_window("01/01/2026", "02/03/2026")
    with pytest.raises(ValueError):
        search_window("02/11/2026", "01/11/2026")


def test_large_page_size_500_is_transmitted_and_501_is_rejected():
    client = FakeClient({"data": {"list": [], "totalNum": 0}})
    result = fetch_checkins(
        client, "2026-11-03T00:00:00-03:00",
        "2026-11-04T00:00:00-03:00", page=1, page_size=500)
    assert result["page_size"] == 500
    # Lotes grandes são agregados sem solicitar tamanho acima de 100
    # a servidores HikCentral antigos.
    assert client.calls[0][1]["pageSize"] == 100
    with pytest.raises(ValueError, match="Página"):
        fetch_checkins(client, "2026-11-03T00:00:00-03:00",
                       "2026-11-04T00:00:00-03:00", page_size=501)


def test_logical_page_500_aggregates_five_server_pages():
    class Paged:
        def __init__(self):
            self.calls = []
        def post(self, path, payload):
            self.calls.append((path, dict(payload)))
            count = payload["pageSize"]
            start_id = (payload["pageNo"] - 1) * count
            return {"data": {
                "totalNum": 650,
                "list": [
                    example_record(visitor=str(start_id+i+1), record=str(start_id+i+1))
                    for i in range(min(count, 650 - start_id))
                ],
            }}
    client = Paged()
    result = fetch_checkins(
        client, "2026-11-03T00:00:00-03:00",
        "2026-11-04T00:00:00-03:00", page=1, page_size=500)
    assert len(result["rows"]) == 500
    assert result["has_next"] and result["total"] == 650
    assert [p["pageNo"] for _, p in client.calls] == [1, 2, 3, 4, 5]
    second = fetch_checkins(
        client, "2026-11-03T00:00:00-03:00",
        "2026-11-04T00:00:00-03:00", page=2, page_size=500)
    assert len(second["rows"]) == 150
    assert [p["pageNo"] for _, p in client.calls[5:]] == [6, 7]
    assert not second["has_next"]


def test_paginated_checkin_allows_multiple_visits_for_same_visitor():
    records = [example_record(record="40"), example_record(record="41")]
    client = FakeClient({"code": "0", "data": {
        "list": records, "totalNum": 250,
    }})
    result = fetch_checkins(
        client, "2026-11-01T00:00:00-03:00", "2026-11-03T00:00:00-03:00",
        page=1, name="Maria")
    assert len(result["rows"]) == 2 and result["has_next"]
    assert all(r["door_ids"] == [] and r["access_names"] == [] for r in result["rows"])
    assert result["rows"][0]["group"] == "Hóspedes Vale"
    path, request, opts = client.calls[0]
    assert path == CHECKINS_PATH and request["orderType"] == "1"
    assert request["visitorName"] == "Maria" and not opts


def test_never_guess_missing_identifiers_or_privileges():
    assert normalize_checkin({"visitorBaseInfo": {}}) is None
    item = normalize_checkin(example_record())
    assert item["visitor_id"] == "812"
    assert item["registration_record_id"] == "40"
    assert item["photo_url"] == "opaque-photo-id"
    assert not item["access_names"] and not item["door_ids"]


def test_invalid_server_response_is_not_silently_treated_as_empty():
    client = FakeClient({"data": {"error": "not a list"}})
    with pytest.raises(HikError, match="lista paginada"):
        fetch_checkins(client, "2026-11-01T00:00:00-03:00",
                       "2026-11-03T00:00:00-03:00")


def test_detail_lookup_is_read_only():
    client = FakeClient({"data": {"visitorId": "812", "visitorGroupName": "Hóspedes Vale"}})
    assert visitor_details(client, "812")["visitorId"] == "812"
    assert client.calls[0][0] == DETAIL_PATH


def test_photo_download_passes_opaque_reference_only_to_artemis():
    output = io.BytesIO()
    Image.new("RGB", (40, 30)).save(output, "JPEG")
    data_uri = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    client = FakeClient({"code": "0", "data": data_uri})
    picture = fetch_visitor_photo(client, "812", "photo-ref")
    assert picture[:3] == b"\xff\xd8\xff"
    path, payload, options = client.calls[0]
    assert path == PHOTO_PATH
    assert payload == {"visitorId": "812", "picType": "0", "picUrl": "photo-ref"}
    assert options == {"allow_image_data": True}


def test_malicious_picture_and_bad_content_rejected():
    with pytest.raises(HikError):
        decode_photo_response("data:text/html;base64,SGVsbG8=")
    with pytest.raises(HikError):
        decode_photo_response("data:image/jpeg;base64," + base64.b64encode(b"not a jpeg").decode())
    with pytest.raises(HikError):
        fetch_visitor_photo(FakeClient({}), "812", "x"*260)


def test_remote_history_has_no_automatic_write_endpoints():
    import inspect
    import remote_history
    source = inspect.getsource(remote_history)
    assert "registerment/update" not in source
    assert "auth/reapplication" not in source
    assert "single/addPersons" not in source

def test_remote_register_time_is_not_the_visit_validity_time():
    row = example_record()
    row["registerTime"] = "2026-11-02T13:19:42-03:00"
    result = normalize_checkin(row)
    assert result["registered_at"] == "2026-11-02T13:19:42-03:00"
    assert result["entry_at"] == "2026-11-02T12:57:00-03:00"
    from remote_history import history_time
    assert history_time(result["registered_at"]) == "02/11/2026 13:19:42"
    assert history_time(result["entry_at"]) == "02/11/2026 12:57:00"
    assert history_time("") == "—"


def test_real_access_level_is_recovered_from_unique_matching_reservation():
    from remote_history import parse_access_levels, enrich_checkins_with_appointment_levels
    from remote_history import APPOINTMENTS_PATH
    record = normalize_checkin(example_record())
    appointment = {
        "appointID": "appt-9",
        "appointStartTime": record["entry_at"],
        "appointEndTime": record["exit_at"],
        "visitorInfo": {
            "visitorId": "812", "visitorGroupName": "Hóspedes Vale",
            "accessInfo": {"accessLevelList": [
                {"accessLevel": {"id": "level-3", "baseInfo": {"name": "VISITANTE_VALE"}}}
            ]},
        },
    }
    assert parse_access_levels(appointment) == [{"id": "level-3", "name": "VISITANTE_VALE"}]
    fake = FakeClient({"data": {"total": 1, "list": [appointment]}})
    output = enrich_checkins_with_appointment_levels(
        fake, [record], "2026-11-01T00:00:00-03:00", "2026-11-04T00:00:00-03:00")
    assert output[0]["access_names"] == ["VISITANTE_VALE"]
    assert output[0]["access_level_ids"] == ["level-3"]
    assert fake.calls[0][0] == APPOINTMENTS_PATH


def test_never_infer_level_from_group_on_ambiguous_or_unmatched_appointments():
    from remote_history import enrich_checkins_with_appointment_levels
    record = normalize_checkin(example_record())
    same_person_wrong_day = {
        "appointStartTime": "2026-11-01T12:57:00-03:00",
        "appointEndTime": record["exit_at"],
        "visitorInfo": {"visitorId": "812", "accessInfo": {"accessLevelList": [
            {"accessLevel": {"id": "3", "baseInfo": {"name": "VISITANTE_VALE"}}}]}},
    }
    fake = FakeClient({"data": {"total": 1, "list": [same_person_wrong_day]}})
    result = enrich_checkins_with_appointment_levels(
        fake, [record], "2026-11-01T00:00:00-03:00", "2026-11-04T00:00:00-03:00")
    assert result[0]["access_names"] == []
    assert result[0]["access_level_ids"] == []  # campo presente, mas sem nível inventado


def test_remote_edit_requires_original_checkin_id_and_level_id():
    from remote_history import resolve_remote_edit_record
    record = normalize_checkin(example_record())
    appointment = {
        "appointID": "9", "appointStartTime": record["entry_at"],
        "appointEndTime": record["exit_at"],
        "visitorInfo": {"visitorId": "812", "visitorGroupName": "Hóspedes Vale",
                        "accessInfo": {"accessLevelList": [
                            {"accessLevel": {"id": "10", "baseInfo": {"name": "VISITANTE_VALE"}}}]}},
    }
    class Client:
        def __init__(self, registered="40", level_id="10"):
            self.registered = registered
            self.level_id = level_id
            self.writes = []
        def find_registration_record(self, *args, active_only=False, record_id=""):
            assert active_only is False
            return example_record(record=self.registered)
        def post(self, path, body, **kw):
            if path.endswith("appointmentlist"):
                return {"data": {"list": [appointment], "total": 1}}
            if path.endswith("single/visitorinfo"):
                return {"data": {"visitorId": "812"}}
            self.writes.append(path)
            raise AssertionError("O teste não pode alterar um visitante real.")
        def resolve_levels(self, names):
            return ([{"id": self.level_id, "name": "VISITANTE_VALE",
                      "elements": [{"Element": {"ID": str(i)}} for i in range(3)]}],
                    [str(i) for i in range(3)])

    verified, details = resolve_remote_edit_record(Client(), record)
    assert verified["_remote_verified"] is True
    assert verified["_original_appointment_id"] == "9"
    assert verified["_original_checkin_id"] == "40"
    assert verified["access_names"] == ["VISITANTE_VALE"]
    assert verified["door_ids"] == ["0", "1", "2"]
    assert details["visitorId"] == "812"
    with pytest.raises(HikError, match="check-in original mudou"):
        resolve_remote_edit_record(Client(registered="different"), record)
    with pytest.raises(HikError, match="ID do nível"):
        resolve_remote_edit_record(Client(level_id="different"), record)


@pytest.mark.parametrize("selected_status,present", [
    ("0", True), ("1", True), ("2", True), ("3", True), ("4", True), ("1", False),
], ids=["active", "checked-out", "auto-out", "terminal-out", "late-out", "missing-selected"])
def test_open_editor_locates_selected_checkin_in_any_state(monkeypatch, selected_status, present):
    """O ID selecionado distingue visitas antigas e ativas no mesmo intervalo."""
    from hikcentral import HikCentral
    from remote_history import APPOINTMENTS_PATH, resolve_remote_edit_record

    selected_row = example_record(record="selected-checkin")
    selected_row["visitorStatus"] = selected_status
    other = example_record(record="other-checkin")
    other["visitorStatus"] = "1" if selected_status == "0" else "0"
    remote_rows = [other, selected_row] if present else [other]
    calls = []
    api = HikCentral("127.0.0.1", 443, "test-key", "test-secret")

    def read_only_post(path, body):
        calls.append(path)
        if path == CHECKINS_PATH:
            return {"data": {"list": remote_rows, "totalNum": len(remote_rows)}}
        if path == APPOINTMENTS_PATH:
            return {"data": {"list": [], "total": 0}}
        if path == DETAIL_PATH:
            return {"data": {
                "visitorId": "812", "visitorGroupName": "Hóspedes Vale",
                "accessInfo": {"accessLevelList": [{"accessLevel": {
                    "id": "10", "baseInfo": {"name": "VISITANTE_VALE"}}}]},
            }}
        pytest.fail("A abertura do editor deve fazer somente consultas simuladas.")

    monkeypatch.setattr(api, "post", read_only_post)
    monkeypatch.setattr(api, "resolve_levels", lambda names: (
        [{"id": "10", "name": "VISITANTE_VALE",
          "elements": [{"Element": {"ID": str(i)}} for i in range(3)]}],
        [str(i) for i in range(3)]))
    selected = normalize_checkin(selected_row)
    if not present:
        with pytest.raises(HikError, match="check-in original mudou"):
            resolve_remote_edit_record(api, selected)
        assert calls == [CHECKINS_PATH]
        return

    verified, _ = resolve_remote_edit_record(api, selected)
    assert verified["_remote_verified"] is True
    assert verified["_original_checkin_id"] == "selected-checkin"
    assert verified["_update_record_id"] == "selected-checkin"
    assert verified["_original_appointment_id"] == ""
    assert set(calls) <= {CHECKINS_PATH, APPOINTMENTS_PATH, DETAIL_PATH}


def test_search_general_visitor_information_by_name_includes_person_without_checkin():
    from remote_history import DIRECTORY_PATH, search_visitor_directory
    client = FakeClient({"data": {"VisitorInfoList": {"VisitorInfo": [
        {"VisitorInfo": {
            "visitorId": "901", "visitorGivenName": "FABRICIO ROGERIO CAMPOS",
            "visitorFamilyName": "LEMOS", "visitorGroupName": "Hóspedes Vale",
            "smallPhotoUrl": "photo-ref",
        }},
        {"VisitorInfo": {
            "visitorId": "902", "visitorGivenName": "BRUNO",
            "visitorFamilyName": "SILVA", "visitorGroupName": "Day use",
        }},
    ]}, "total": 2}})
    rows = search_visitor_directory(client, "FABRICIO")
    assert len(rows) == 1
    assert rows[0]["visitor_id"] == "901"
    assert rows[0]["full_name"] == "FABRICIO ROGERIO CAMPOS LEMOS"
    assert rows[0]["checkin_state"] == "sem check-in no período"
    assert rows[0]["registration_record_id"] == ""
    assert rows[0]["entry_at"] == "" and rows[0]["exit_at"] == ""
    assert rows[0]["origin"] == "VisitorInformation"
    path, payload, options = client.calls[0]
    assert path == DIRECTORY_PATH
    assert payload["searchCriteria"] == {"personName": "FABRICIO"}
    assert payload["pageNo"] == 1 and not options


def test_directory_supports_list_and_never_guesses_visitor_id_from_name():
    from remote_history import search_visitor_directory
    client = FakeClient({"data": {"list": [
        {"VisitorInfo": {"visitorGivenName": "FABRICIO", "visitorFamilyName": "SILVA"}},
        {"VisitorInfo": {"visitorId": "123", "visitorGivenName": "FABRICIO", "visitorFamilyName": "LEMOS"}},
    ], "total": 2}})
    rows = search_visitor_directory(client, "fabricio")
    assert [x["visitor_id"] for x in rows] == ["123"]


def test_active_visitor_can_use_exact_level_from_visitor_information_when_appointment_missing():
    from remote_history import resolve_remote_edit_record
    record = normalize_checkin(example_record())
    class ExistingVisitor:
        def find_registration_record(self, *args, active_only=False, record_id=""):
            assert active_only is False
            return example_record()
        def post(self, path, payload):
            if path.endswith("appointmentlist"):
                return {"data": {"list": [], "total": 0}}
            if path.endswith("single/visitorinfo"):
                return {"data": {
                    "visitorId": "812", "visitorGroupName": "Hóspedes Vale",
                    "accessInfo": {"accessLevelList": [
                        {"accessLevel": {"id": "10", "baseInfo": {"name": "VISITANTE_VALE"}}}
                    ]}}}
            raise AssertionError("Não deve haver nenhum POST de escrita.")
        def resolve_levels(self, names):
            assert names == ["VISITANTE_VALE"]
            return ([{"id": "10", "name": "VISITANTE_VALE",
                      "elements": [{"Element": {"ID": str(i)}} for i in range(3)]}],
                    [str(i) for i in range(3)])
    verified, details = resolve_remote_edit_record(ExistingVisitor(), record)
    assert verified["_remote_verified"] is True
    assert verified["_level_verified_from"] == "Visitor Information"
    assert verified["access_level_ids"] == ["10"]
    assert details["visitorId"] == "812"


def test_missing_level_returns_recovery_requirement_before_remote_writes():
    from remote_history import resolve_remote_edit_record
    record = normalize_checkin(example_record())
    class MissingLevel:
        def find_registration_record(self, *args, active_only=False, record_id=""):
            assert active_only is False
            return example_record()
        def post(self, path, payload):
            if path.endswith("appointmentlist"):
                return {"data": {"list": [], "total": 0}}
            if path.endswith("single/visitorinfo"):
                return {"data": {"visitorId": "812", "visitorGroupName": "Hóspedes Vale"}}
            raise AssertionError("Never call write endpoints")
    with pytest.raises(HikError, match="não confirmou um único nível"):
        resolve_remote_edit_record(MissingLevel(), record)


def test_name_and_date_window_share_same_checkin_request():
    """TOALHEIRO 2 com validade no dia seguinte nunca entra por Visitor Information."""
    from remote_history import filter_local_history_by_entry_date, DIRECTORY_PATH
    start, end = search_window("05/11/2026", "05/11/2026")
    current = example_record(visitor="1701", record="701", name="TESTE TOALHEIRO 1")
    current["visitorBaseInfo"]["visitStartTime"] = "2026-11-05T00:00:00-03:00"
    current["visitorBaseInfo"]["visitEndTime"] = "2026-11-05T23:59:59-03:00"
    client = FakeClient({"code": "0", "data": {"list": [current], "totalNum": 1}})
    found = fetch_checkins(client, start, end, page=1, name="TOALHEIRO", page_size=10)
    assert len(found["rows"]) == 1
    assert found["rows"][0]["visitor_id"] == "1701"
    assert len(client.calls) == 1
    path, params, options = client.calls[0]
    assert path == CHECKINS_PATH and path != DIRECTORY_PATH
    assert params["visitorName"] == "TOALHEIRO"
    assert params["visitStartTime"] == "2026-11-05T00:00:00-03:00"
    assert params["visitEndTime"] == "2026-11-06T00:00:00-03:00"
    assert options == {}


def test_offline_date_name_uses_validity_start_not_registration_timestamp():
    """Cadastrar no dia 05 não inclui ingresso que começa apenas no dia 06."""
    from remote_history import filter_local_history_by_entry_date
    start, end = search_window("05/11/2026", "05/11/2026")
    rows = [
        {"visitor_id": "1654", "first": "Teste", "last": "Toalheiro 1",
         "registered_at": "2026-11-05T14:00:37-03:00",
         "entry_at": "2026-11-05T00:00:00-03:00"},
        {"visitor_id": "1655", "first": "Teste", "last": "Toalheiro 2",
         "registered_at": "2026-11-05T14:01:00-03:00",
         "entry_at": "2026-11-06T00:00:00-03:00"},
        {"visitor_id": "1656", "first": "Outro", "last": "Visitante",
         "registered_at": "2026-11-05T10:00:00-03:00",
         "entry_at": "2026-11-05T00:00:00-03:00"},
        {"visitor_id": "1657", "first": "Teste", "last": "Sem data",
         "registered_at": "2026-11-05T10:00:00-03:00", "entry_at": ""},
    ]
    selected = filter_local_history_by_entry_date(rows, start, end, "tEsTe")
    assert [r["visitor_id"] for r in selected] == ["1654"]
    tomorrow_start, tomorrow_end = search_window("06/11/2026", "06/11/2026")
    tomorrow = filter_local_history_by_entry_date(
        rows, tomorrow_start, tomorrow_end, "toalheiro")
    assert [r["visitor_id"] for r in tomorrow] == ["1655"]


def test_offline_date_name_accepts_normalized_accents_but_not_bare_register_time():
    from remote_history import filter_local_history_by_entry_date
    start, end = search_window("05/11/2026", "05/11/2026")
    rows = [
        {"visitor_id": "1", "full_name": "JOÃO DE SÁ",
         "registered_at": "2026-11-06T08:00:00-03:00",
         "entry_at": "2026-11-05T00:00:00-03:00"},
        {"visitor_id": "2", "full_name": "João de Sá",
         "registered_at": "2026-11-05T08:00:00-03:00",
         "entry_at": ""},
    ]
    assert [x["visitor_id"] for x in filter_local_history_by_entry_date(
        rows, start, end, "joao de sa")] == ["1"]


def test_history_tab_does_not_mix_general_directory_with_filtered_checkins(monkeypatch):
    """Integração offline do controller: nome+De/Até, sem consultar Visitor Information."""
    from types import SimpleNamespace
    import app
    import modern_ui
    import remote_history

    current = example_record(
        visitor="1654", record="250", name="TESTE TOALHEIRO 1")
    current["visitorBaseInfo"]["visitStartTime"] = "2026-11-05T00:00:00-03:00"
    current["visitorBaseInfo"]["visitEndTime"] = "2026-11-05T23:59:59-03:00"
    next_day = example_record(
        visitor="1655", record="251", name="TESTE TOALHEIRO 2")
    next_day["visitorBaseInfo"]["visitStartTime"] = "2026-11-06T00:00:00-03:00"
    next_day["visitorBaseInfo"]["visitEndTime"] = "2026-11-06T23:59:59-03:00"
    # Simula API com semântica de sobreposição, que devolve também 06/11.
    # O app precisa filtrar novamente para cumprir exatamente o recorte.
    client = FakeClient({"code": "0", "data": {
        "list": [current, next_day], "totalNum": 2}})
    monkeypatch.setattr(app, "list_history", lambda: [
        {"visitor_id": "1655", "entry_at": "2026-11-06T00:00:00-03:00",
         "registered_at": "2026-11-05T14:15:00-03:00",
         "first": "TESTE", "last": "TOALHEIRO 2"}])
    monkeypatch.setattr(remote_history, "search_visitor_directory",
                        lambda *args, **kwargs: pytest.fail(
                            "Visitor Information nao deve fazer parte da lista"))
    monkeypatch.setattr(modern_ui, "start_history_loading", lambda *a, **kw: None)
    monkeypatch.setattr(modern_ui, "stop_history_loading", lambda *a, **kw: None)

    class Value:
        def __init__(self, value=""):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value

    class Control:
        def configure(self, **kwargs):
            self.params = kwargs

    ui = SimpleNamespace(
        key="fake-key", secret="fake-secret", busy=False,
        _history_page=1,
        history_start=Value("05/11/2026"),
        history_end=Value("05/11/2026"),
        history_name=Value("TOALHEIRO"),
        history_page_size=Value("10"),
        history_status=Value(), history_page_label=Value(),
        history_previous_button=Control(), history_next_button=Control(),
    )
    captured = {}
    ui.client = lambda: client
    ui._show_history_rows = lambda rows, label: captured.update(
        rows=rows, label=label)
    ui._load_history_passages = lambda rows, label: captured.update(
        passages=rows)
    ui.task = lambda action, finish: finish(action(), None)
    app.ValeApp.refresh_history(ui)
    assert [r["visitor_id"] for r in captured["rows"]] == ["1654"]
    assert [r["visitor_id"] for r in captured["passages"]] == ["1654"]
    assert "descartado" in captured["label"]
    assert len(client.calls) == 2
    path, payload, options = client.calls[0]
    assert path == CHECKINS_PATH
    assert payload["visitorName"] == "TOALHEIRO"
    assert payload["visitStartTime"] == "2026-11-05T00:00:00-03:00"
    assert payload["visitEndTime"] == "2026-11-06T00:00:00-03:00"
    assert options == {}
    reservation_path, reservation_payload, _ = client.calls[1]
    assert reservation_path.endswith("appointment/appointmentlist")
    assert reservation_payload["visitorName"] == "TOALHEIRO"


def test_original_appointment_identifier_not_checkin_id_or_reservation_code():
    from remote_history import original_appointment_identifier
    assert original_appointment_identifier({"appointRecordId": "appt-7155",
                                            "reservationCode": "7155"}) == "appt-7155"
    assert original_appointment_identifier({"appointID": "appt-legacy"}) == "appt-legacy"
    assert original_appointment_identifier({"appointId": "appt-camel"}) == "appt-camel"
    assert original_appointment_identifier({"recordId": "check-1654",
                                            "reservationCode": "7155"}) == ""
    with pytest.raises(HikError, match="divergentes"):
        original_appointment_identifier({"appointRecordId": "a",
                                         "appointID": "b"})


def test_live_reservation_level_shape_with_lowercase_baseinfo_is_read():
    from remote_history import parse_access_levels
    actual_shape = {"accessInfo": {"accessLevelList": {"accessLevel": [
        {"id": 7, "baseinfo": {"name": "VISITANTE_VALE"}}]}}}
    assert parse_access_levels(actual_shape) == [{"id": "7", "name": "VISITANTE_VALE"}]


def test_linked_booking_is_found_by_both_ids_after_checkin_period_changed():
    from remote_history import find_original_appointment, reservation_period_report, reservation_period_notice
    booking = {"appointID": "booking-1780", "appointCode": "3911",
               "appointStartTime": "2026-11-06T00:00:00-03:00",
               "appointEndTime": "2026-11-06T23:59:59-03:00",
               "visitorInfo": {"visitorId": "1780"}}
    record = {"visitor_id": "1780", "appointment_id": "booking-1780",
              "entry_at": "2026-11-07T00:00:00-03:00",
              "exit_at": "2026-11-08T23:59:59-03:00"}
    client = FakeClient({"data": {"list": [booking], "total": 1}})
    assert find_original_appointment(client, record) == booking
    period = reservation_period_report(client, record, record["entry_at"], record["exit_at"])
    assert period["state"] == "different"
    notice = reservation_period_notice(period, record["entry_at"], record["exit_at"])
    assert "Visita (Check-In/Out): 07/11/2026" in notice
    assert "Reserva original 3911: 06/11/2026" in notice
    assert "diferentes" in notice
    # A matching ID never authorizes associating a booking owned by another person.
    booking["visitorInfo"]["visitorId"] = "other-visitor"
    assert reservation_period_report(client, record, record["entry_at"], record["exit_at"])["state"] == "unavailable"


def test_original_period_metadata_remains_queryable_after_later_edits():
    from remote_history import find_original_appointment
    booking = {"appointID": "booking-1780", "appointCode": "3911",
               "appointStartTime": "2026-11-06T00:00:00-03:00",
               "appointEndTime": "2026-11-06T23:59:59-03:00",
               "visitorInfo": {"visitorId": "1780"}}
    record = {"visitor_id": "1780", "appointment_id": "booking-1780",
              "entry_at": "2026-11-20T00:00:00-03:00", "exit_at": "2026-11-21T23:59:59-03:00",
              "reservation_entry_at": booking["appointStartTime"], "reservation_exit_at": booking["appointEndTime"]}
    client = FakeClient({"data": {"list": [booking], "total": 1}})
    assert find_original_appointment(client, record) == booking
    assert client.calls[0][1]["appointStartTime"] == "2026-11-05T00:00:00-03:00"


def test_checkin_link_and_local_reference_preserve_the_selected_visit():
    from remote_history import add_local_booking_reference

    item = example_record()
    item.update(appointId="512", appointRecordId="40")
    current = normalize_checkin(item)
    assert current["registration_record_id"] == "40"
    assert current["appointment_id"] == "512"
    assert normalize_checkin({**item, "appointId": ""})["appointment_id"] == ""
    latest = {"visitor_id": "812", "registration_record_id": "42", "appointment_id": "999",
              "reservation_code": "1234"}
    assert add_local_booking_reference(current, latest) == current
    assert add_local_booking_reference(current, {**latest, "registration_record_id": "40"}) == current
    old = {**current, "appointment_id": ""}
    assert add_local_booking_reference(old, latest) == old
    recovered = add_local_booking_reference(old, {**latest, "registration_record_id": "40"})
    assert recovered["appointment_id"] == "999" and recovered["reservation_code"] == "1234"


def test_checkin_history_enrichment_reads_period_from_exact_reservation():
    from remote_history import APPOINTMENTS_PATH, enrich_checkins_with_reservation_periods

    checkin_source = example_record(visitor="1780", record="checkin-1780")
    checkin_source["visitorBaseInfo"].update(
        fullName="Wesley1 Nascimento2",
        visitStartTime="2026-11-07T00:00:00-03:00",
        visitEndTime="2026-11-08T23:59:59-03:00")
    checkin = normalize_checkin(checkin_source)
    booking = {
        "appointID": "booking-8738", "appointCode": "8738", "appointStatus": "3",
        "appointStartTime": "2026-11-07T00:00:00-03:00",
        "appointEndTime": "2026-11-09T23:59:59-03:00",
        "visitorInfo": {"visitorId": "1780", "visitorName": "Wesley1 Nascimento2"},
    }
    client = FakeClient({"data": {"list": [booking], "total": 1}})

    result = enrich_checkins_with_reservation_periods(
        client, [checkin], [{
            "visitor_id": "1780", "registration_record_id": "checkin-1780",
            "appointment_id": "booking-8738",
        }], "2026-11-07T00:00:00-03:00", "2026-11-09T00:00:00-03:00")

    assert result[0]["entry_at"] == "2026-11-07T00:00:00-03:00"
    assert result[0]["exit_at"] == "2026-11-08T23:59:59-03:00"
    assert result[0]["reservation_entry_at"] == "2026-11-07T00:00:00-03:00"
    assert result[0]["reservation_exit_at"] == "2026-11-09T23:59:59-03:00"
    assert result[0]["reservation_code"] == "8738"
    assert client.calls[0][0] == APPOINTMENTS_PATH
    assert client.calls[0][1]["appointState"] == -1


def test_checkin_history_does_not_guess_reservation_without_exact_link():
    from remote_history import enrich_checkins_with_reservation_periods

    checkin_source = example_record(visitor="1780", record="checkin-1780")
    checkin_source["visitorBaseInfo"].update(
        visitStartTime="2026-11-07T00:00:00-03:00",
        visitEndTime="2026-11-08T23:59:59-03:00")
    checkin = normalize_checkin(checkin_source)
    client = FakeClient({"data": {"list": [], "total": 0}})

    result = enrich_checkins_with_reservation_periods(
        client, [checkin], [], "2026-11-07T00:00:00-03:00",
        "2026-11-09T00:00:00-03:00")

    assert result[0]["reservation_entry_at"] == ""
    assert result[0]["reservation_exit_at"] == ""
    assert result[0]["_reservation_period"]["state"] == "unavailable"
    assert len(client.calls) == 1  # tenta resolver a reserva pela OpenAPI


def test_history_finds_unique_reservation_by_visitor_when_checkin_has_no_link():
    from remote_history import APPOINTMENTS_PATH, enrich_checkins_with_reservation_periods

    source = example_record(visitor="1923", record="checkin-1923")
    source["visitorStatus"] = "1"  # saída/checkout
    source["visitorBaseInfo"].update(
        fullName="Wesley1 Nascimento2",
        visitStartTime="2026-11-07T00:00:00-03:00",
        visitEndTime="2026-11-08T23:59:59-03:00")
    checkin = normalize_checkin(source)
    booking = {
        "appointId": "booking-8738", "appointCode": "8738", "appointStatus": "3",
        "appointStartTime": "2026-11-07T00:00:00-03:00",
        "appointEndTime": "2026-11-09T23:59:59-03:00",
        "VisitorInfo": {"visitorId": "1923", "visitorGroupName": "Hóspedes Vale"},
    }
    client = FakeClient({"data": {"list": [booking], "total": 1}})

    result = enrich_checkins_with_reservation_periods(
        client, [checkin], [], "2026-11-07T00:00:00-03:00", "2026-11-10T00:00:00-03:00",
        name="wes")

    assert result[0]["reservation_entry_at"] == booking["appointStartTime"]
    assert result[0]["reservation_exit_at"] == booking["appointEndTime"]
    assert result[0]["reservation_code"] == "8738"
    assert result[0]["appointment_id"] == "booking-8738"
    assert client.calls[0][0] == APPOINTMENTS_PATH
    assert client.calls[0][1]["visitorName"] == "wes"


def test_closed_checkin_can_find_its_unique_overlapping_reservation_for_edit():
    from remote_history import find_original_appointment

    booking = {
        "appointId": "booking-8738", "appointCode": "8738", "appointStatus": "3",
        "appointStartTime": "2026-11-07T00:00:00-03:00",
        "appointEndTime": "2026-11-09T23:59:59-03:00",
        "VisitorInfo": {"visitorId": "1923", "visitorGroupName": "Hóspedes Vale"},
    }
    record = {
        "visitor_id": "1923", "entry_at": "2026-11-07T00:00:00-03:00",
        "exit_at": "2026-11-08T23:59:59-03:00", "group": "Hóspedes Vale",
        "checkin_state": "check-out", "first": "Wesley1",
    }
    client = FakeClient({"data": {"list": [booking], "total": 1}})
    assert find_original_appointment(client, record) == booking
    assert client.calls[0][1]["visitorName"] == "Wesley1"


def test_history_leaves_reservation_dates_blank_when_same_visitor_has_two_possible_bookings():
    from remote_history import enrich_checkins_with_reservation_periods

    source = example_record(visitor="1923", record="checkin-1923")
    source["visitorBaseInfo"].update(
        visitStartTime="2026-11-07T00:00:00-03:00",
        visitEndTime="2026-11-08T23:59:59-03:00")
    checkin = normalize_checkin(source)
    bookings = [
        {"appointID": f"booking-{index}", "appointCode": index, "appointStatus": "3",
         "appointStartTime": "2026-11-07T00:00:00-03:00",
         "appointEndTime": f"2026-11-{day}T23:59:59-03:00",
         "visitorInfo": {"visitorId": "1923", "visitorGroupName": "Hóspedes Vale"}}
        for index, day in ((1, "09"), (2, "10"))
    ]
    client = FakeClient({"data": {"list": bookings, "total": 2}})

    result = enrich_checkins_with_reservation_periods(
        client, [checkin], [], "2026-11-07T00:00:00-03:00", "2026-11-10T00:00:00-03:00")

    assert result[0]["reservation_entry_at"] == ""
    assert result[0]["reservation_exit_at"] == ""
    assert result[0]["_reservation_period"]["state"] == "unavailable"


def _test_appointment(aid, vid, *, status=0, code="6650"):
    return {"appointID": aid, "appointCode": code, "appointStatus": status,
            "appointStartTime": "2026-11-02T12:57:00-03:00",
            "appointEndTime": "2026-11-03T17:00:00-03:00",
            "visitorInfo": {"visitorId": vid, "visitorGivenName": "Maria",
                            "visitorFamilyName": "Alves", "visitorGroupName": "Hóspedes Vale",
                            "remark": "18"}}


def test_active_visit_with_extended_dates_finds_the_unique_registered_booking():
    from remote_history import find_original_appointment

    appointment = _test_appointment("512", "812", status=3)
    other_visitor = _test_appointment("999", "999", status=3)
    api = FakeClient({"data": {"total": 2, "list": [appointment, other_visitor]}})
    record = normalize_checkin(example_record())
    assert record["exit_at"] != appointment["appointEndTime"]
    assert find_original_appointment(api, record) == appointment


def test_divergent_dates_with_multiple_bookings_require_explicit_selection():
    from remote_history import find_original_appointment

    appointment = _test_appointment("512", "812", status=3)
    second = _test_appointment("513", "812", status=3, code="6651")
    other_visitor = _test_appointment("999", "999", status=3)
    api = FakeClient({"data": {"total": 3, "list": [appointment, other_visitor, second]}})
    record = normalize_checkin(example_record())
    # This could be a different reservation whose dates happen to match the
    # period the operator already set in Check-In/Out.
    second["appointEndTime"] = record["exit_at"]
    with pytest.raises(HikError, match="Selecione a reserva correta") as raised:
        find_original_appointment(api, record)
    assert raised.value.choices["appointment"] == [appointment, second]
    assert "appointment" in raised.value.fields
    assert find_original_appointment(api, {**record, "appointment_id": "513"}) == second


def test_reserved_only_search_uses_appointment_endpoint_and_keeps_no_checkin():
    from remote_history import APPOINTMENTS_PATH, fetch_unchecked_reservations

    appointment = _test_appointment("512", "812")
    checked_in = _test_appointment("513", "813", status=3, code="6651")
    client = FakeClient({"data": {"total": 2, "list": [appointment, checked_in]}})
    start, end = search_window("02/11/2026", "04/11/2026")
    result = fetch_unchecked_reservations(client, start, end, name="Maria", page_size=10)
    assert result["total"] == 2 and not result["has_next"]
    row, = result["rows"]
    assert row["_reservation_only"] is True and row["registration_record_id"] == ""
    assert row["visitor_id"] == "812" and row["appointment_id"] == "512"
    assert row["reservation_code"] == "6650" and row["reservation"] == "18"
    assert row["full_name"] == "Maria Alves" and row["group"] == "Hóspedes Vale"
    assert row["_reservation_period"]["state"] == "matched"
    assert row["entry_at"] == appointment["appointStartTime"]
    assert row["exit_at"] == appointment["appointEndTime"]
    path, params, opts = client.calls[0]
    assert path == APPOINTMENTS_PATH and not opts
    assert params["appointState"] == 0 and params["visitorName"] == "Maria"
    assert params["appointStartTime"] == start and params["appointEndTime"] == end
    assert params["pageNo"] == 1 and params["pageSize"] == 10


def test_combined_history_shows_checkins_and_reservations_together_with_real_register_time():
    from remote_history import APPOINTMENTS_PATH

    checkin = example_record(visitor="812", record="check-40")
    checkin["registerTime"] = "2026-11-02T13:19:42-03:00"
    booking = _test_appointment("512", "813")
    calls = []

    class Client:
        def post(self, path, payload, **options):
            calls.append((path, dict(payload)))
            if path == CHECKINS_PATH:
                return {"data": {"totalNum": 1, "list": [checkin]}}
            assert path == APPOINTMENTS_PATH
            return {"data": {"total": 1, "list": [booking]}}

    start, end = search_window("02/11/2026", "04/11/2026")
    result = fetch_combined_history(Client(), start, end, name="Maria", page_size=10)

    assert len(result["rows"]) == 2
    assert result["total"] == 2 and not result["has_next"]
    checkin_row = next(row for row in result["rows"] if not row.get("_reservation_only"))
    reservation_row = next(row for row in result["rows"] if row.get("_reservation_only"))
    assert checkin_row["registered_at"] == checkin["registerTime"]
    assert reservation_row.get("registered_at", "") == ""
    assert reservation_row["appointment_id"] == "512"
    assert {path for path, _payload in calls} == {CHECKINS_PATH, APPOINTMENTS_PATH}


def test_reservation_display_uses_given_and_family_names_when_server_joins_them():
    from remote_history import _normalize_unchecked_reservation

    appointment = {
        "appointID": "774457976919425024", "appointCode": "8738", "appointStatus": "0",
        "appointStartTime": "2026-11-07T00:00:00-03:00",
        "appointEndTime": "2026-11-09T23:59:59-03:00",
        "visitorInfo": {
            "visitorId": "1923", "visitorGivenName": "WESLEYN1",
            "visitorFamilyName": "TESTE2", "visitorName": "WESLEYN1TESTE2",
        },
    }

    row = _normalize_unchecked_reservation(appointment)

    assert (row["first"], row["last"]) == ("WESLEYN1", "TESTE2")
    assert row["full_name"] == "WESLEYN1 TESTE2"


def test_reservation_without_group_keeps_its_single_visitor_access_level():
    from remote_history import _normalize_unchecked_reservation

    appointment = {
        "appointID": "774759018718560256", "appointCode": "2899", "appointStatus": "0",
        "appointStartTime": "2026-11-08T19:15:13-03:00",
        "appointEndTime": "2026-11-08T23:59:59-03:00",
        "visitorInfo": {"visitorId": "2114", "visitorGivenName": "Wesley",
                        "visitorFamilyName": "Teste", "remark": "123"},
        "accessInfo": {"accessLevelList": [{"accessLevel": {
            "id": 1, "baseInfo": {"name": "ENTRADA ACQUAVALE"}}}]},
    }

    row = _normalize_unchecked_reservation(appointment)

    assert row["group"] == ""
    assert row["access_names"] == ["ENTRADA ACQUAVALE"]
    assert row["access_level_ids"] == ["1"]
