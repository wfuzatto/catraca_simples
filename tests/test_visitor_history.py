"""Encrypted history regression without accessing real Windows credentials or HikCentral."""
import json
import os
import sys
import types
import pytest
from visitor_history import save_registration, get_history, list_history, set_history_status

def test_history_is_encrypted_deduplicated_and_does_not_persist_credentials(tmp_path, monkeypatch):
    import visitor_history
    monkeypatch.setattr(visitor_history, "os", types.SimpleNamespace(name="nt", replace=os.replace))
    # Deterministic stub stands in for Windows DPAPI in isolated unit tests.
    def protect(data, *args): return b"FAKE_ENCRYPTED:" + data[::-1]
    def unprotect(data, *args): return (None, data.split(b":", 1)[1][::-1])
    monkeypatch.setitem(sys.modules, "win32crypt", types.SimpleNamespace(
        CryptProtectData=protect, CryptUnprotectData=unprotect))
    visitor = {
        "visitor_id": "v-123", "appointment_id": "app-2", "registration_record_id": "reg-8",
        "first": "Teste", "last": "Visitante", "group": "Hóspedes Vale",
        "reservation": "201", "door_ids": ["access1", "access2", "access3"],
        "access_names": ["VISITANTE_VALE"],
        "entry_at": "2026-11-02T09:00:00-03:00",
        "exit_at": "2026-11-04T17:00:00-03:00",
        "expect_face": True, "expect_card": True,
        "state": "queued", "checkin_state": "não verificado",
        "card_number": "SENSITIVE-CARD", "photo_jpeg": "SENSITIVE-FACE",
        "app_secret": "SENSITIVE-SECRET"
    }
    save_registration(visitor, tmp_path)
    encrypted = (tmp_path / "visitor_history.dpapi").read_bytes()
    for secret in (b"Teste", b"SENSITIVE-CARD", b"SENSITIVE-FACE", b"SENSITIVE-SECRET"):
        assert secret not in encrypted
    assert get_history("v-123", tmp_path)["first"] == "Teste"
    assert "card_number" not in get_history("v-123", tmp_path)
    set_history_status("v-123", state="confirmed", checkin_state="confirmado", base=tmp_path)
    save_registration({**visitor, "state": "confirmed"}, tmp_path)
    assert len(list_history(tmp_path)) == 1
    assert get_history("v-123", tmp_path)["checkin_state"] == "confirmado"
    # A verified edit may normalize the legacy local label to the actual
    # active check-in status without being discarded as an older snapshot.
    save_registration({**visitor, "state": "confirmed",
                       "checkin_state": "check-in efetuado"}, tmp_path)
    assert get_history("v-123", tmp_path)["checkin_state"] == "check-in efetuado"
    with pytest.raises(ValueError):
        set_history_status("unknown", state="failed", base=tmp_path)


def test_history_no_plaintext_fallback_on_non_windows(tmp_path):
    import visitor_history
    if os.name == "nt":
        pytest.skip("Linux fail-closed path")
    with pytest.raises(RuntimeError, match="Windows"):
        save_registration({"visitor_id":"v", "door_ids":["d"],"entry_at":"a",
                           "exit_at":"b","access_names":["level"]},tmp_path)
