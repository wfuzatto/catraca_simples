"""Regression: never leave a Vale visitor in generic Visitors after editing.

All API responses below are simulated; no real HikCentral write is performed.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from edit_errors import EditorFieldError
from visitor_editor import choose_editor_group
from visitor_profile import (PERSON_INFO, PERSON_UPDATE, update_profile,
                             confirm_visitor_group_after_edit)
from remote_history import DETAIL_PATH
from visitor_remark import _original_group_from_level
from unregistered_reservation import _request


class FakeGroupBackend:
    def __init__(self):
        self.group = "Hóspedes Vale"
        self.name = ("Nome", "Sobrenome")
        self.cards = [{"cardNo": "22143"}]
        self.writes = []

    def visitor_group_catalog(self):
        return [{"id": "1", "name": "Visitors"},
                {"id": "3", "name": "Hóspedes Vale"},
                {"id": "4", "name": "Hóspedes Serra"}]

    def post(self, path, payload):
        if path == PERSON_INFO:
            return {"data": {
                "personId": "2139", "personGivenName": self.name[0],
                "personFamilyName": self.name[1], "cards": deepcopy(self.cards),
                "orgIndexCode": {"Visitors": "1", "Hóspedes Vale": "3",
                                 "Hóspedes Serra": "4"}[self.group]}}
        if path == DETAIL_PATH:
            return {"data": {"VisitorInfo": {
                "visitorId": "2139", "visitorGivenName": self.name[0],
                "visitorFamilyName": self.name[1],
                "visitorGroupName": self.group, "remark": "3321"}}}
        assert path == PERSON_UPDATE
        self.writes.append(deepcopy(payload))
        if "personGivenName" in payload:
            self.name = (payload["personGivenName"], payload["personFamilyName"])
        if "cards" in payload:
            self.cards = deepcopy(payload["cards"])
        if "orgIndexCode" in payload:
            self.group = { "1": "Visitors", "3": "Hóspedes Vale",
                           "4": "Hóspedes Serra"}[str(payload["orgIndexCode"])]
        else:
            # Real failure mode: any partial person/profile/card write
            # silently reverts the orgIndexCode.
            self.group = "Visitors"
        return {"code": "0", "data": {}}


def test_profile_name_and_card_write_restores_original_group_last():
    api = FakeGroupBackend()
    changed = update_profile(api, "2139", "Nome Novo", "Sobrenome",
                             card="221430", group="Hóspedes Vale")
    assert api.group == "Hóspedes Vale"
    assert "grupo" in changed
    assert api.writes[-1] == {"personId": "2139", "orgIndexCode": "3"}
    assert confirm_visitor_group_after_edit(api, "2139", "Hóspedes Vale") == "Hóspedes Vale"


def test_final_group_check_recovers_after_another_nfc_write():
    api = FakeGroupBackend()
    api.post(PERSON_UPDATE, {"personId": "2139", "cards": [{"cardNo": "221430"}]})
    assert api.group == "Visitors"
    assert confirm_visitor_group_after_edit(api, "2139", "Hóspedes Vale") == "Hóspedes Vale"
    assert api.writes[-1]["orgIndexCode"] == "3"


def test_selected_new_group_is_respected_not_original():
    api = FakeGroupBackend()
    assert confirm_visitor_group_after_edit(api, "2139", "Hóspedes Serra") == "Hóspedes Serra"
    assert api.group == "Hóspedes Serra"


def test_explicit_visitor_information_disagreement_blocks_success():
    api = FakeGroupBackend()
    original_post = api.post
    def inconsistent(path, payload):
        if path == DETAIL_PATH:
            result = original_post(path, payload)
            result["data"]["VisitorInfo"]["visitorGroupName"] = "Visitors"
            return result
        return original_post(path, payload)
    api.post = inconsistent
    with pytest.raises(EditorFieldError, match="NÃO foi confirmado"):
        confirm_visitor_group_after_edit(api, "2139", "Hóspedes Vale")


def test_generic_visitors_cannot_be_considered_an_approved_group():
    with pytest.raises(EditorFieldError, match="grupo Visitor autorizado"):
        confirm_visitor_group_after_edit(FakeGroupBackend(), "2139", "Visitors")


def test_chooser_recovers_from_visitors_only_with_matching_approved_level():
    record = {"group": "Hóspedes Vale", "access_names": ["VISITANTE_VALE"]}
    assert choose_editor_group("Visitors", record) == "Hóspedes Vale"
    assert choose_editor_group("Hóspedes Serra", record) == "Hóspedes Serra"
    assert choose_editor_group("Visitors", {**record, "access_names": ["OTHER"]}) == "Visitors"
    assert choose_editor_group("Visitors", {"group": "Visitors",
                                           "access_names": ["VISITANTE_VALE"]}) == "Hóspedes Vale"


def test_remark_update_payload_uses_group_from_original_level_not_visitors():
    booking = {
        "visitorInfo": {"visitorId": "2139", "visitorGivenName": "Nome",
                        "visitorFamilyName": "Sobrenome",
                        "visitorGroupName": "Visitors"},
        "accessInfo": {"accessLevelList": [{"accessLevel": {
            "id": "23", "baseInfo": {"name": "VISITANTE_VALE"}}}]},
        "appointRecordId": "1000",
        "appointStartTime": "2099-11-09T00:00:00-03:00",
        "appointEndTime": "2099-11-09T23:59:59-03:00",
    }
    group = _original_group_from_level(booking, {
        "group": "Hóspedes Vale", "access_names": ["VISITANTE_VALE"]})
    assert group == "Hóspedes Vale"
    api = FakeGroupBackend()
    api.group = "Visitors"
    api.access_info = lambda levels: {"accessLevelList": levels}
    payload = _request(api, booking, "2139",
                       booking["appointStartTime"], booking["appointEndTime"],
                       "33210", group, None)
    assert payload["visitorInfoList"][0]["VisitorInfo"]["visitorGroupName"] == "Hóspedes Vale"


def test_remark_access_conflict_blocks_write():
    booking = {"accessInfo": {"accessLevelList": [{"accessLevel": {
        "id": "23", "baseInfo": {"name": "VISITANTE_VALE"}}}]}}
    with pytest.raises(EditorFieldError, match="diverge"):
        _original_group_from_level(booking, {"group": "Hóspedes Serra"})
