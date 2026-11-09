"""Simula as duas fontes independentes de credencial NFC, sem rede real."""
from types import SimpleNamespace

import pytest

from credential_management import (
    CardReadback, PERSON_INFO, PERSON_UPDATE, VISITOR_INFO,
    read_card_credential, ensure_issued_card)
from hikcentral import HikError


class FakeHik:
    def __init__(self, *, person_cards=None, visitor_cards=None,
                 visitor_has_field=True, person_has_field=True, refuse_write=False):
        self.person_cards = person_cards if person_cards is not None else []
        self.visitor_cards = visitor_cards if visitor_cards is not None else []
        self.visitor_has_field = visitor_has_field
        self.person_has_field = person_has_field
        self.refuse_write = refuse_write
        self.writes = []

    def post(self, path, payload):
        assert payload.get("personId", payload.get("visitorId")) == "2139"
        if path == PERSON_INFO:
            data = {"personId": "2139"}
            if self.person_has_field:
                data["cards"] = [{"cardNo": value} for value in self.person_cards]
            return {"code": "0", "data": data}
        if path == VISITOR_INFO:
            info = {"visitorId": "2139"}
            if self.visitor_has_field:
                info["cards"] = [{"cardNo": value} for value in self.visitor_cards]
            return {"code": "0", "data": {"VisitorInfo": info}}
        if path == PERSON_UPDATE:
            self.writes.append(payload)
            if self.refuse_write:
                return {"code": "0", "data": None}
            self.person_cards = [item["cardNo"] for item in payload["cards"]]
            self.visitor_cards = list(self.person_cards)
            return {"code": "0", "data": None}
        raise AssertionError("Unexpected endpoint " + path)


def test_zero_cards_in_credential_management_is_not_saved_card():
    api = FakeHik(person_cards=[], visitor_cards=["22143"])
    state = read_card_credential(api, "2139")
    assert state.person_card == ()
    assert state.visitor_card == ("22143",)
    assert not state.confirmed


def test_existing_credential_is_read_only_when_both_sources_agree():
    api = FakeHik(person_cards=["22143"], visitor_cards=["22143"])
    assert ensure_issued_card(api, "2139", "22143").confirmed
    assert api.writes == []


def test_missing_card_is_issued_to_same_visitor_and_verified():
    api = FakeHik(person_cards=[], visitor_cards=["22143"])
    state = ensure_issued_card(api, "2139", "22143")
    assert state.confirmed
    assert api.writes == [{
        "personId": "2139", "cards": [{"cardNo": "22143"}]
    }]


def test_person_confirms_but_visitor_does_not_get_card():
    api = FakeHik(person_cards=["22143"], visitor_cards=[], refuse_write=True)
    with pytest.raises(HikError, match="Visitor Information"):
        ensure_issued_card(api, "2139", "22143")
    assert len(api.writes) == 1


def test_no_false_success_if_hikcentral_silently_ignores_card_update():
    api = FakeHik(person_cards=[], visitor_cards=["22143"], refuse_write=True)
    with pytest.raises(HikError, match="Credential Management"):
        ensure_issued_card(api, "2139", "22143")
    assert len(api.writes) == 1


def test_optional_cards_not_in_person_response_gets_inserted():
    api = FakeHik(person_has_field=False, person_cards=[], visitor_cards=[])
    # Este mock insere na escrita e devolve o campo na releitura.
    old_post = api.post
    def post(path, payload):
        if path == PERSON_UPDATE:
            api.person_has_field = True
        return old_post(path, payload)
    api.post = post
    assert ensure_issued_card(api, "2139", "22143").confirmed


def test_visitor_api_does_not_expose_cards_person_is_only_evidence():
    api = FakeHik(person_cards=["22143"], visitor_has_field=False)
    state = read_card_credential(api, "2139")
    assert state.visitor_card is None
    assert state.person_card == ("22143",)


def test_never_update_without_matching_visitor_id():
    api = FakeHik()
    api.post = lambda path, payload: {"data": {"personId": "someone-else"}} if path == PERSON_INFO else {}
    with pytest.raises(HikError, match="identidade"):
        ensure_issued_card(api, "2139", "22143")


def test_reject_more_than_one_card_without_overwrite():
    api = FakeHik(person_cards=["123", "456"], visitor_cards=["123", "456"])
    with pytest.raises(HikError, match="outra credencial|mais de uma"):
        ensure_issued_card(api, "2139", "22143")
    assert api.writes == []
