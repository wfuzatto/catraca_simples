"""Pendência: diagnóstico sem dados pessoais e liberação somente confirmada."""
import json
from pathlib import Path

import pytest

from core import mark_appointment_inflight, save_pending, mark_registered, load_pending
from pending_recovery import read_pending_diagnostics, archive_pending_after_manual_review


def test_inflight_has_no_visitor_id_and_never_offers_automatic_resend(tmp_path):
    mark_appointment_inflight(tmp_path)
    info = read_pending_diagnostics(tmp_path)
    assert info["stage"] == "appointment_inflight"
    assert not info["visitor_id"]
    assert not info["appointment_id"]
    assert info["can_resume"] is False
    assert "Não reenviar automaticamente" in info["description"]
    assert info["modified_at"]
    assert not info["registration_record_id"]


def test_confirmed_appointment_can_be_reconciled_by_existing_ids(tmp_path):
    save_pending("visitor-100", ["level-1"], tmp_path,
                 appointment_id="reservation-100", door_ids=["door-1"],
                 registered=False, expect_face=True, expect_card=False,
                 entry_at="2099-11-01T09:00:00-03:00",
                 exit_at="2099-11-02T17:00:00-03:00",
                 access_names=["VISITANTE_VALE"])
    info = read_pending_diagnostics(tmp_path)
    assert info["can_resume"] is True
    assert info["visitor_id"] == "visitor-100"
    assert info["appointment_id"] == "reservation-100"
    assert info["door_count"] == 1
    assert info["access_names"] == ["VISITANTE_VALE"]
    assert info["registered"] is False


def test_archiving_requires_explicit_acknowledgement_and_keeps_audit_file(tmp_path):
    mark_appointment_inflight(tmp_path)
    original = (tmp_path / "pending.json").read_bytes()
    info = read_pending_diagnostics(tmp_path)
    with pytest.raises(ValueError, match="CONFERIDO"):
        archive_pending_after_manual_review(info["sha256"], "", tmp_path)
    assert (tmp_path / "pending.json").exists()
    archived = archive_pending_after_manual_review(info["sha256"], "CONFERIDO", tmp_path)
    assert archived.exists()
    assert archived.name.startswith("pending_reviewed_")
    assert archived.read_bytes() == original
    assert not (tmp_path / "pending.json").exists()
    assert read_pending_diagnostics(tmp_path) is None


def test_manual_review_never_discards_a_changed_pending_operation(tmp_path):
    save_pending("visitor-100", ["level-1"], tmp_path,
                 appointment_id="reservation-100", door_ids=["door-1"],
                 registered=False, expect_face=False, expect_card=True,
                 entry_at="2099-11-01T09:00:00-03:00",
                 exit_at="2099-11-02T17:00:00-03:00",
                 access_names=["VISITANTE_VALE"])
    old = read_pending_diagnostics(tmp_path)
    mark_registered("record-new", tmp_path)
    with pytest.raises(RuntimeError, match="mudou"):
        archive_pending_after_manual_review(old["sha256"], "CONFERIDO", tmp_path)
    assert load_pending(tmp_path)["registration_record_id"] == "record-new"
    assert not list(tmp_path.glob("pending_reviewed_*.json"))
