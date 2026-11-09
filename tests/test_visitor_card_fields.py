from types import SimpleNamespace

import pytest

import visitor_profile as profiles
from edit_errors import EditorFieldError
from hikcentral import HikError


def test_reservation_card_replaces_virtual_card_and_is_verified(monkeypatch):
    stored = [{"cardNo": "VIRTUAL-RESERVATION-CARD"}]
    writes = []
    monkeypatch.setattr(profiles, "visitor_details", lambda *_: {"VisitorInfo": {
        "visitorId": "2118", "visitorGivenName": "Nome", "visitorFamilyName": "Teste"}})

    def post(path, payload):
        if path == profiles.PERSON_INFO:
            return {"data": {"personId": "2118", "personGivenName": "Nome",
                             "personFamilyName": "Teste", "cards": list(stored)}}
        assert path == profiles.PERSON_UPDATE
        writes.append(payload)
        stored[:] = payload["cards"]

    client = SimpleNamespace(post=post)
    profiles.save_reserved_profile_card(client, "2118", "Nome", "Teste", "12345678")
    profiles.save_reserved_profile_card(client, "2118", "Nome", "Teste", "12345678")
    assert writes == [{"personId": "2118", "cards": [{"cardNo": "12345678"}]}]
    profiles.save_reserved_profile_card(client, "2118", "Nome", "Teste", "12345679")
    assert writes[-1] == {"personId": "2118", "cards": [{"cardNo": "12345679"}]}
    assert len(writes) == 2


def test_reservation_card_refuses_only_when_profile_identity_is_different(monkeypatch):
    monkeypatch.setattr(profiles, "read_profile", lambda *_: (
        {}, ("Outro", "Perfil"), ("VIRTUAL",), "", False))
    with pytest.raises(EditorFieldError) as error:
        profiles.save_reserved_profile_card(SimpleNamespace(), "2118", "Nome", "Teste", "12345678")
    assert "card" in error.value.fields


@pytest.mark.parametrize("saved_card", ["12345678", ""])
def test_active_edit_verifies_checkin_card_and_visitor_remark(monkeypatch, saved_card):
    start, end = "2026-11-09T00:00:00-03:00", "2026-11-10T23:59:59-03:00"
    record = {"visitor_id": "2119", "registration_record_id": "419",
              "entry_at": start, "exit_at": end, "group": "Hóspedes Vale"}
    state = {"card": "3210", "remark": "123"}
    writes = []

    def lookup(vid, begin, finish, *, active_only=False, record_id=""):
        assert (vid, begin, finish) == ("2119", start, end)
        assert active_only
        assert record_id in {"419", ""}
        return {"recordId": "419", "visitorStatus": "0",
                "visitorBaseInfo": {"visitorGroup": "Hóspedes Vale"},
                "visitorCardInfo": {"cardNumber": state["card"]}}

    def update(rid, vid, payload, levels):
        assert (rid, vid) == ("419", "2119")
        assert levels == [{"id": "3", "name": "VISITANTE_VALE"}]
        assert (payload["visitStartTime"], payload["visitEndTime"]) == (start, end)
        info = payload["visitorInfoList"][0]["VisitorInfo"]
        writes.append(info)
        state.update(card=saved_card, remark=info["remark"])
        raise HikError("Resposta incerta após gravar")

    client = SimpleNamespace(find_registration_record=lookup, update_registered_visitor=update)
    monkeypatch.setattr(profiles, "read_profile", lambda *_: (
        {"VisitorInfo": {"visitorId": "2119", "companyName": "Vale"}},
        ("Nome", "Teste"), (), state["remark"], True))
    monkeypatch.setattr(profiles, "read_visitor_remark", lambda *_: state["remark"])
    # Card tests mock the profile API; group restoration is covered by
    # tests/test_visitor_group_preservation.py.
    monkeypatch.setattr(profiles, "set_visitor_group", lambda *a: False)
    monkeypatch.setattr(profiles, "resolve_remote_edit_record", lambda *_: (
        {"access_level_ids": ["3"], "access_names": ["VISITANTE_VALE"]}, {}))
    if saved_card:
        assert profiles.update_active_visitor_fields(client, record, card="12345678", remark="456") == ["pulseira", "Remark"]
        assert profiles.update_active_visitor_fields(client, record, card="12345678", remark="456") == []
    else:
        with pytest.raises(EditorFieldError) as error:
            profiles.update_active_visitor_fields(client, record, card="12345678", remark="456")
        assert set(error.value.fields) == {"card"}
        assert error.value.profile_saved == ["Remark"]
    assert len(writes) == 1
    assert writes[0]["cards"] == [{"cardNo": "12345678"}]
    assert writes[0]["companyName"] == "Vale"
