"""Regressões de recuperação: sem requisições reais ao HikCentral."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from core import (load_pending, mark_appointment_inflight, save_pending,
                  mark_checkin_started, mark_registered)
from hikcentral import HikError
import registration_recovery as recovery


START = "2099-11-09T00:00:00-03:00"
END = "2099-11-09T23:59:59-03:00"


def secure_context(monkeypatch):
    # Módulo simulado: os testes também funcionam fora do Windows.
    def protect(data, *args):
        return b"mock-dpapi:" + bytes(data)[::-1]

    def unprotect(data, *args):
        if not data.startswith(b"mock-dpapi:"):
            raise ValueError("invalid ciphertext")
        return ("Mock", data[len(b"mock-dpapi:"):][::-1])

    monkeypatch.setitem(sys.modules, "win32crypt", SimpleNamespace(
        CryptProtectData=protect, CryptUnprotectData=unprotect))
    return recovery.seal_registration_context(
        first="Wesley", last="Nascimento", group="Hóspedes Vale",
        card="32131231231231", photo=b"fake-image")


def test_dpapi_context_is_opaque_and_roundtrips(monkeypatch):
    token = secure_context(monkeypatch)
    assert token.startswith("dpapi:v1:")
    assert "32131231231231" not in token
    assert "Wesley" not in token
    loaded = recovery.open_registration_context(token)
    assert loaded["card"] == "32131231231231"
    assert loaded["photo_bytes"] == b"fake-image"
    with pytest.raises(ValueError, match="protegido"):
        recovery.open_registration_context("Wesley:32131231231231")


def test_checkpoint_carries_protected_context_without_exposing_card(monkeypatch, tmp_path):
    token = secure_context(monkeypatch)
    mark_appointment_inflight(tmp_path, encrypted_context=token)
    save_pending("2138", ["level-1"], tmp_path, appointment_id="reservation-2138",
                 door_ids=["entrada", "saida", "servico"],
                 expect_face=True, expect_card=True,
                 entry_at=START, exit_at=END,
                 access_names=["VISITANTE_VALE"],
                 checkin_requested=True, card_confirmed=False,
                 registered=False)
    saved = load_pending(tmp_path)
    assert saved["encrypted_context"] == token
    assert saved["checkin_inflight"] is False
    assert saved["card_confirmed"] is False
    raw = (tmp_path / "pending.json").read_text(encoding="utf-8")
    assert "32131231231231" not in raw
    assert "Wesley" not in raw
    mark_checkin_started(tmp_path)
    with pytest.raises(RuntimeError, match="incerto"):
        mark_checkin_started(tmp_path)
    mark_registered("check-2138", tmp_path)
    assert load_pending(tmp_path)["registration_record_id"] == "check-2138"


def fake_booking(first="Wesley", last="Nascimento", group="Hóspedes Vale"):
    return {
        "appointRecordId": "reservation-2138",
        "appointStartTime": START, "appointEndTime": END,
        "visitorInfo": {"visitorId": "2138", "visitorGivenName": first,
                        "visitorFamilyName": last, "visitorGroupName": group},
    }


def pending():
    return {
        "visitor_id": "2138", "appointment_id": "reservation-2138",
        "entry_at": START, "exit_at": END,
        "expect_card": True, "checkin_requested": True,
        "registered": True, "registration_record_id": "check-2138",
    }


def context():
    return {
        "first": "Wesley", "last": "Nascimento",
        "group": "Hóspedes Vale", "card": "32131231231231",
        "photo_bytes": b"jpeg",
    }


def test_verified_reservation_recovers_profile_and_checkin_card(monkeypatch):
    seen = []
    monkeypatch.setattr(recovery, "find_original_appointment",
                        lambda client, record: fake_booking())
    import credential_management
    monkeypatch.setattr(credential_management, "ensure_issued_card",
                        lambda *a: None)
    import visitor_profile
    monkeypatch.setattr(visitor_profile, "update_profile",
                        lambda *a, **kw: seen.append(("profile", a, kw)))
    monkeypatch.setattr(visitor_profile, "confirm_checkin_card",
                        lambda *a: seen.append(("checkin_card", a)))
    assert recovery.restore_pending_credentials(object(), pending(), context())
    assert [x[0] for x in seen] == ["profile", "checkin_card"]
    assert seen[0][2]["card"] == "32131231231231"
    assert seen[0][2]["group"] == "Hóspedes Vale"
    assert seen[1][1][1:] == (
        "2138", START, END, "check-2138", "32131231231231")


def test_can_restore_profile_before_checkin_without_marking_card(monkeypatch):
    seen = []
    monkeypatch.setattr(recovery, "find_original_appointment",
                        lambda client, record: fake_booking())
    import credential_management
    monkeypatch.setattr(credential_management, "ensure_issued_card",
                        lambda *a: None)
    import visitor_profile
    monkeypatch.setattr(visitor_profile, "update_profile",
                        lambda *a, **kw: seen.append("profile"))
    monkeypatch.setattr(visitor_profile, "confirm_checkin_card",
                        lambda *a: pytest.fail("check-in not yet confirmed"))
    record = {**pending(), "registered": False, "registration_record_id": ""}
    assert recovery.restore_pending_credentials(
        object(), record, context(), verify_checkin_card=False)
    assert seen == ["profile"]


@pytest.mark.parametrize("first,last,group", [
    ("Outra", "Pessoa", "Hóspedes Vale"),
    ("Wesley", "Nascimento", "Day use"),
    ("", "Nascimento", "Hóspedes Vale"),
])
def test_identity_or_group_conflict_never_modifies_remote(monkeypatch, first, last, group):
    monkeypatch.setattr(recovery, "find_original_appointment",
                        lambda client, record: fake_booking(first, last, group))
    import visitor_profile
    monkeypatch.setattr(visitor_profile, "update_profile",
                        lambda *a, **kw: pytest.fail("unsafe remote write"))
    with pytest.raises(HikError, match="Identidade|Grupo"):
        recovery.restore_pending_credentials(object(), pending(), context())


def test_appointment_mismatch_fails_closed(monkeypatch):
    wrong = {**fake_booking(), "appointRecordId": "other-reservation"}
    monkeypatch.setattr(recovery, "find_original_appointment",
                        lambda client, record: wrong)
    with pytest.raises(HikError, match="Reserva"):
        recovery.verify_pending_reservation(object(), pending(), context())



def test_virtual_card_is_fixed_on_existing_checkin_without_new_reservation(monkeypatch):
    from edit_errors import EditorFieldError
    import visitor_profile
    calls = []
    attempts = {"count": 0}

    def confirm(*args):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise EditorFieldError({"card": "Pulseira virtual"})
        calls.append(("confirmed", args))

    def read_record(*args, **kwargs):
        assert kwargs == {"active_only": True, "record_id": "check-2138"}
        return {"recordId": "check-2138",
                "visitorBaseInfo": {"visitorGroup": "Hóspedes Vale"}}

    def update(*args, **kwargs):
        calls.append(("updated", args, kwargs))

    monkeypatch.setattr(visitor_profile, "confirm_checkin_card", confirm)
    monkeypatch.setattr(visitor_profile, "update_active_visitor_fields", update)
    api = SimpleNamespace(find_registration_record=read_record)
    recovery.ensure_checkin_card(api, pending(), context())
    assert [item[0] for item in calls] == ["updated", "confirmed"]
    assert calls[0][2]["card"] == "32131231231231"
    assert calls[0][1][1]["registration_record_id"] == "check-2138"


def test_virtual_card_does_not_update_unverified_checkin(monkeypatch):
    from edit_errors import EditorFieldError
    import visitor_profile
    monkeypatch.setattr(visitor_profile, "confirm_checkin_card",
                        lambda *a: (_ for _ in ()).throw(
                            EditorFieldError({"card": "Pulseira virtual"})))
    monkeypatch.setattr(visitor_profile, "update_active_visitor_fields",
                        lambda *a, **kw: pytest.fail("wrong check-in must not be edited"))
    api = SimpleNamespace(find_registration_record=lambda *a, **kw: {
        "recordId": "checkin-of-another-visitor"})
    with pytest.raises(EditorFieldError):
        recovery.ensure_checkin_card(api, pending(), context())
