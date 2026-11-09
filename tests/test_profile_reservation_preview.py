from types import SimpleNamespace

import pytest

import reservation_replacement as replacement
from hikcentral import HikCentral, HikError
from remote_history import DETAIL_PATH, APPOINTMENTS_PATH
from edit_errors import EditorFieldError
from visitor_profile import (PERSON_INFO, PERSON_UPDATE, read_profile, read_visitor_remark, update_profile,
                             read_visitor_group, set_visitor_group, validate_visitor_remark)


def test_apartment_uses_visitor_information_and_does_not_write():
    calls = []

    def post(path, payload):
        calls.append((path, payload))
        return {"data": {"VisitorInfo": {"visitorId": "1865", "remark": " 321 "}}}

    assert read_visitor_remark(SimpleNamespace(post=post), "1865") == "321"
    assert calls == [(DETAIL_PATH, {"visitorId": "1865"})]
    with pytest.raises(HikError, match="Visitor ID"):
        read_visitor_remark(SimpleNamespace(post=post), "1999")


def test_apartment_validates_the_visitor_api_limit():
    assert validate_visitor_remark(" 321 ") == "321"
    assert validate_visitor_remark(" ") == ""
    assert len(validate_visitor_remark("ã" * 128)) == 128
    with pytest.raises(EditorFieldError) as exc:
        validate_visitor_remark("A" * 129)
    assert "remark" in exc.value.fields


def test_profile_updates_name_and_card_without_touching_visit_or_booking():
    names = ["Nome antigo", "Sobrenome antigo"]
    cards = ["1111"]
    writes = []

    class Client:
        def post(self, path, payload):
            if path == DETAIL_PATH:
                return {"data": {"visitorId": "1865", "visitorGivenName": names[0],
                                 "visitorFamilyName": names[1]}}
            if path == PERSON_INFO:
                return {"data": {"personId": "1865", "personGivenName": names[0],
                                 "personFamilyName": names[1],
                                 "cards": [{"cardNo": cards[0]}]}}
            assert path == PERSON_UPDATE
            writes.append(payload)
            names[:] = [payload["personGivenName"], payload["personFamilyName"]]
            cards[:] = [payload["cards"][0]["cardNo"]]
            return {"code": "0"}

    client = Client()
    assert update_profile(client, "1865", "Nome novo", "Sobrenome novo", card="2222") == ["nome e sobrenome", "pulseira"]
    assert writes == [{"personId": "1865", "personGivenName": "Nome novo",
                       "personFamilyName": "Sobrenome novo", "cards": [{"cardNo": "2222"}]}]
    assert update_profile(client, "1865", "Nome novo", "Sobrenome novo", card="2222") == []
    assert len(writes) == 1


def test_profile_editor_moves_misfiled_card_from_document_to_nfc_card():
    state = {"first": "Mauro", "last": "Bello", "cards": [],
             "certificateNo": "2884250122"}
    writes = []

    class Client:
        def post(self, path, payload):
            if path == DETAIL_PATH:
                return {"data": {"VisitorInfo": {
                    "visitorId": "1870", "visitorGivenName": state["first"],
                    "visitorFamilyName": state["last"],
                    "certificateNo": state["certificateNo"],
                }}}
            if path == PERSON_INFO:
                return {"data": {
                    "personId": "1870", "personGivenName": state["first"],
                    "personFamilyName": state["last"], "cards": list(state["cards"]),
                    "certificateNo": state["certificateNo"],
                }}
            assert path == PERSON_UPDATE
            writes.append(payload.copy())
            state["first"] = payload.get("personGivenName", state["first"])
            state["last"] = payload.get("personFamilyName", state["last"])
            state["cards"] = payload.get("cards", state["cards"])
            state["certificateNo"] = payload.get("certificateNo", state["certificateNo"])
            return {"code": "0"}

    changed = update_profile(Client(), "1870", "Mauro", "Bello", card="2884250122")

    assert changed == ["pulseira"]
    assert writes == [{"personId": "1870", "cards": [{"cardNo": "2884250122"}],
                       "certificateNo": ""}]
    assert state["cards"] == [{"cardNo": "2884250122"}]
    assert state["certificateNo"] == ""


def test_profile_group_uses_person_org_index_code_and_is_verified():
    state = {"group_id": "1"}
    writes = []

    class Client:
        def visitor_group_catalog(self):
            return [{"id": "1", "name": "Visitors"},
                    {"id": "3", "name": "Hóspedes Vale"}]

        def post(self, path, payload):
            if path == PERSON_INFO:
                return {"data": {"personId": "1865", "orgIndexCode": state["group_id"]}}
            assert path == PERSON_UPDATE
            writes.append(payload)
            state["group_id"] = str(payload["orgIndexCode"])
            return {"code": "0"}

    client = Client()
    assert read_visitor_group(client, "1865") == "Visitors"
    assert set_visitor_group(client, "1865", "Hóspedes Vale") is True
    assert state["group_id"] == "3"
    assert read_visitor_group(client, "1865") == "Hóspedes Vale"
    assert writes == [{"personId": "1865", "orgIndexCode": "3"}]
    assert set_visitor_group(client, "1865", "Hóspedes Vale") is False
    assert len(writes) == 1


@pytest.mark.parametrize("visitor_remark, expected", [("Apto visitante", ("Apto visitante", True)),
                                                      (None, ("", False))])
def test_profile_remark_comes_from_visitor_information(visitor_remark, expected):
    visitor = {"visitorId": "1865", "visitorGivenName": "Nome", "visitorFamilyName": "Teste"}
    if visitor_remark is not None:
        visitor["remark"] = visitor_remark

    def post(path, _payload):
        if path == DETAIL_PATH:
            return {"data": {"VisitorInfo": visitor}}
        assert path == PERSON_INFO
        return {"data": {"personId": "1865", "personGivenName": "Nome", "personFamilyName": "Teste",
                         "remark": "Apto pessoa", "cards": [{"cardNo": "1234"}]}}

    assert read_profile(SimpleNamespace(post=post), "1865")[2:] == (("1234",), *expected)


def test_reservation_group_update_targets_visitor_info_and_preserves_its_dates():
    from unregistered_reservation import _request

    original = {
        "appointRecordId": "775",
        "appointStartTime": "2026-11-08T00:00:00-03:00",
        "appointEndTime": "2026-11-09T23:59:59-03:00",
        "appointStatus": "0",
        "visitorInfo": {"visitorId": "1865", "visitorGivenName": "Wesley",
                        "visitorFamilyName": "Teste", "visitorGroupName": "Hóspedes Vale",
                        "remark": "321"},
    }

    class Client:
        def post(self, path, payload):
            assert path == DETAIL_PATH
            assert payload == {"visitorId": "1865"}
            return {"data": {"VisitorInfo": {
                "visitorId": "1865", "visitorGivenName": "Wesley",
                "visitorFamilyName": "Teste", "visitorGroupName": "Hóspedes Vale"}}}

        @staticmethod
        def access_info(levels):
            return {"accessLevelList": levels}

    body = _request(
        Client(), original, "1865", original["appointStartTime"], original["appointEndTime"],
        "321", "Hóspedes Serra", {"id": "8", "name": "VISITANTE_SERRA"},
        expected_profile_group="Hóspedes Vale")
    info = body["visitorInfoList"][0]["VisitorInfo"]
    assert info["visitorGroupName"] == "Hóspedes Serra"
    assert info["remark"] == "321"
    assert body["appointStartTime"] == original["appointStartTime"]
    assert body["appointEndTime"] == original["appointEndTime"]


def test_checkin_lookup_accepts_explicit_empty_total_without_list():
    client = SimpleNamespace(post=lambda *_args: {"data": {"totalNum": 0, "pageIndex": 1}})
    assert HikCentral.find_registration_record(
        client, "1865", "2026-11-14T00:00:00-03:00", "2026-11-15T23:59:59-03:00") is None


@pytest.fixture
def planned_replacement(monkeypatch, tmp_path):
    old_start, old_end = "2026-11-12T00:00:00-03:00", "2026-11-13T23:59:59-03:00"
    start, end = "2026-11-14T00:00:00-03:00", "2026-11-15T23:59:59-03:00"
    level = {"id": "7", "name": "VISITANTE_VALE"}

    def booking(aid, code, arrival, departure):
        return {"appointID": aid, "appointCode": code, "appointStartTime": arrival, "appointStatus": 0,
                "appointEndTime": departure, "visitorInfo": {"visitorId": "1865"},
                "accessInfo": {"accessLevelList": [level]}}

    old = booking("1000", "2502", old_start, old_end)
    bookings = {"2502": old, "5054": booking("2000", "5054", start, end)}

    class Client:
        writes = []
        keep_old = False

        def post(self, path, payload):
            if path == APPOINTMENTS_PATH:
                code = payload.get("appointCode")
                return {"data": {"list": [bookings[code]]}} if code in bookings else {"data": {"total": 0}}
            assert path == replacement.DELETE_BOOKING
            self.writes.append(payload)
            if not self.keep_old:
                bookings.pop("2502")
            raise HikError("Resposta de exclusão indisponível.")

        def find_registration_record(self, _vid, entry, _end, **_kwargs):
            return {"recordId": "visit-new"} if entry == start else None

        def delivery_report(self, *_args, **_kwargs):
            raise AssertionError("A troca de reserva não deve esperar o relatório de cada catraca")

    client = Client()
    monkeypatch.setattr(replacement, "find_original_appointment", lambda *_args: old)
    monkeypatch.setattr(replacement, "_confirmed_edit", lambda *_args: "visit-new")
    monkeypatch.setattr(replacement, "update_existing_visit",
                        lambda *_args: pytest.fail("Uma visita conferida não deve ser gravada de novo."))
    history = []
    monkeypatch.setattr(replacement, "save_registration", history.append)
    journal = tmp_path / "replacement.json"
    record = {"visitor_id": "1865", "appointment_id": "1000",
              "registration_record_id": "visit-old", "entry_at": old_start, "exit_at": old_end}
    payload = {"visitStartTime": start, "visitEndTime": end}
    visitor = SimpleNamespace(first_name="Teste", last_name="Perfil", reservation="LAB",
                              card_number="2222", photo_jpeg=b"")

    def finish(code="5054"):
        return replacement.finish_replacement(
            client, record, code, payload, [{**level, "elements": []}], ["57"], visitor,
            "Hóspedes Vale", ["VISITANTE_VALE"], journal=journal)

    return client, bookings, history, finish


def test_replacement_finishes_after_checkin_without_waiting_for_each_door(planned_replacement):
    client, bookings, history, finish = planned_replacement
    result = finish()
    assert result["replacement_state"] == "confirmed"
    assert result["state"] == "submitted"
    assert result["operation_completed"] is True
    assert client.writes == [{"appointRecordId": "1000"}]
    assert "2502" not in bookings
    assert history[0]["appointment_id"] == "2000"
    assert history[0]["state"] == "submitted"


def test_replacement_rejects_other_visitor_before_deletion(planned_replacement):
    client, bookings, _history, finish = planned_replacement
    bookings["5054"]["visitorInfo"]["visitorId"] = "9999"
    with pytest.raises(HikError, match="perfil selecionado"):
        finish()
    assert client.writes == []


def test_uncertain_deletion_does_not_repeat(planned_replacement):
    client, _bookings, _history, finish = planned_replacement
    client.delivery = "confirmed"
    client.keep_old = True
    with pytest.raises(HikError, match="ainda lista"):
        finish()
    with pytest.raises(HikError, match="anterior ainda não foi confirmada"):
        finish()
    assert client.writes == [{"appointRecordId": "1000"}]
