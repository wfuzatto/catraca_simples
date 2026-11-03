"""Safety guarantees for locally acknowledged Visitor access-level assignments."""
from datetime import datetime, timezone, timedelta
import os
import pytest

from access_receipt import (
    make_access_receipt, matching_access_receipt, MAX_EVIDENCE_AGE,
)

T0 = datetime(2026, 11, 3, 14, 0, 0, tzinfo=timezone.utc)


def record():
    return {
        "visitor_id": "1009", "registration_record_id": "check-1009",
        "group": "Hóspedes Vale",
        "entry_at": "2026-11-03T09:00:00-03:00",
        "exit_at": "2026-11-03T17:00:00-03:00",
        "access_names": ["VISITANTE_VALE"],
        "door_ids": ["entrada", "saida", "servico"],
    }


def proof():
    return make_access_receipt(
        record(), level_id="10", door_ids=record()["door_ids"], confirmed_at=T0)


def matches(p, rec=None, *, now=None, level_id="10", level_name="VISITANTE_VALE",
            door_ids=None):
    return matching_access_receipt(
        p, rec or record(), level_id=level_id, level_name=level_name,
        door_ids=door_ids or record()["door_ids"],
        now=now or T0 + timedelta(minutes=5))


def test_matching_receipt_from_confirmed_update_is_recognized():
    receipt = proof()
    assert receipt["source"] == "acknowledged_update_and_confirmed_delivery"
    assert receipt["record_id"] == "check-1009"
    assert matches(receipt)


@pytest.mark.parametrize("field,value", [
    ("visitor_id", "other-visitor"),
    ("registration_record_id", "new-check-in"),
    ("group", "Hóspedes Serra"),
    ("entry_at", "2026-11-04T09:00:00-03:00"),
    ("exit_at", "2026-11-04T17:00:00-03:00"),
])
def test_receipt_invalidated_on_any_remote_visit_change(field, value):
    rec = record()
    rec[field] = value
    assert not matches(proof(), rec)


def test_receipt_cannot_be_mistaken_for_access_level_or_door_proof():
    assert not matches(proof(), level_id="11")
    assert not matches(proof(), level_name="DAY_USE")
    assert not matches(proof(), door_ids=["entrada", "saida", "extra"])
    assert not matches(proof(), door_ids=["entrada", "entrada", "servico"])
    assert not matches({}, level_id="10")
    bad = proof()
    bad["source"] = "manual_authorization_only"
    assert not matches(bad)


def test_receipt_expires_and_future_timestamp_does_not_verify():
    assert matches(proof(), now=T0 + MAX_EVIDENCE_AGE)
    assert not matches(proof(), now=T0 + MAX_EVIDENCE_AGE + timedelta(seconds=1))
    assert not matches(proof(), now=T0 - timedelta(seconds=1))


def test_receipt_rejects_untrusted_or_naive_dates():
    bad = record()
    bad["entry_at"] = "2026-11-03T09:00:00"
    assert not matches(proof(), bad)
    bad_receipt = proof()
    bad_receipt["confirmed_at"] = "2026-11-03T14:00:00"
    assert not matches(bad_receipt)


def test_remote_resolution_uses_matching_encrypted_local_receipt(monkeypatch):
    import visitor_history
    from remote_history import resolve_remote_edit_record
    from test_editor_recovery import active_checkin, FakeHik
    monkeypatch.setattr(visitor_history, "get_history",
                        lambda visitor_id: {"access_receipt": proof()})
    # Test helper uses a future visit. Make this receipt belong to that exact
    # current visitor/visit and use current datetime to satisfy the time limit.
    rec = active_checkin()
    receipt = make_access_receipt(rec | {
        "access_names": ["VISITANTE_VALE"],
        "door_ids": ["entrada", "saida", "servico"],
    }, level_id="10", door_ids=["entrada", "saida", "servico"])
    monkeypatch.setattr(visitor_history, "get_history",
                        lambda visitor_id: {"access_receipt": receipt})
    resolved, _ = resolve_remote_edit_record(FakeHik(), rec)
    assert not resolved["_requires_access_confirmation"]
    assert resolved["_access_assurance_type"] == "local_acknowledged"
    assert resolved["_level_verified_from"] == "última reaplicação confirmada nesta estação"


def test_stale_receipt_does_not_suppress_permission_warning(monkeypatch):
    import visitor_history
    from remote_history import resolve_remote_edit_record
    from test_editor_recovery import active_checkin, FakeHik
    rec = active_checkin()
    receipt = make_access_receipt(rec | {
        "access_names": ["VISITANTE_VALE"],
        "door_ids": ["entrada", "saida", "servico"],
    }, level_id="10", door_ids=["entrada", "saida", "servico"],
       confirmed_at=T0 - timedelta(days=12))
    monkeypatch.setattr(visitor_history, "get_history",
                        lambda visitor_id: {"access_receipt": receipt})
    resolved, _ = resolve_remote_edit_record(FakeHik(), rec)
    assert resolved["_requires_access_confirmation"]
    assert resolved["_access_assurance_type"] == "needs_consent"


@pytest.mark.skipif(os.name != "nt", reason="DPAPI Windows profile only")
def test_receipt_is_preserved_encrypted_and_invalidated_on_new_checkin(tmp_path):
    from visitor_history import save_registration, get_history
    base = record()
    base.update(first="Teste", last="Pessoa", expect_face=True, expect_card=True,
                access_receipt=proof())
    save_registration(base, tmp_path)
    raw = (tmp_path / "visitor_history.dpapi").read_bytes()
    assert b"check-1009" not in raw
    assert get_history("1009", tmp_path)["access_receipt"]["record_id"] == "check-1009"

    changed = dict(base)
    changed.pop("access_receipt")
    changed["registration_record_id"] = "check-1010"
    save_registration(changed, tmp_path)
    assert "access_receipt" not in get_history("1009", tmp_path)
