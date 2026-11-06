"""A pending edit belongs to one visitor, never the whole reception computer."""
from hashlib import sha256
import json

import pytest

from edit_journal import select_edit_journal
from core import _write_json_atomic


def test_visitors_have_independent_safe_journal_paths(tmp_path):
    first = select_edit_journal("1738", tmp_path)
    second = select_edit_journal("1649", tmp_path)
    assert first != second
    assert first == tmp_path / "visitor_edits" / (
        sha256(b"1738").hexdigest() + ".json")
    _write_json_atomic(first, {"visitor_id": "1738", "status": "update_inflight"})
    assert first.is_file()
    assert not second.exists()
    # Even a malformed remote identifier cannot become a filesystem path.
    odd = select_edit_journal("../outside", tmp_path)
    assert odd.parent == first.parent
    assert odd.name == sha256(b"../outside").hexdigest() + ".json"


def test_legacy_pending_edit_stays_attached_to_original_visitor(tmp_path):
    legacy = tmp_path / "visitor_edit_inflight.json"
    raw = b'{"visitor_id":"1649","status":"update_inflight"}'
    legacy.write_bytes(raw)
    assert select_edit_journal("1649", tmp_path) == legacy
    assert not (tmp_path / "visitor_edits").exists()
    different = select_edit_journal("1738", tmp_path)
    assert different.parent == tmp_path / "visitor_edits"
    assert different != legacy
    assert legacy.read_bytes() == raw


@pytest.mark.parametrize("content", ["not json", "[]", "null", "{}", '{"visitor_id":""}'])
def test_malformed_legacy_journal_requires_clear_reconciliation(tmp_path, content):
    legacy = tmp_path / "visitor_edit_inflight.json"
    legacy.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError, match="pendente inválido"):
        select_edit_journal("1738", tmp_path)
    assert legacy.read_text(encoding="utf-8") == content
    assert not (tmp_path / "visitor_edits").exists()


def test_journal_selector_rejects_symlink_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "visitor_edits").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    with pytest.raises(RuntimeError, match="Pasta de transações inválida"):
        select_edit_journal("1738", tmp_path)
    assert list(outside.iterdir()) == []


def test_journal_selector_rejects_legacy_symlink(tmp_path):
    actual = tmp_path / "keep.json"
    actual.write_text(json.dumps({"visitor_id": "1738"}), encoding="utf-8")
    try:
        (tmp_path / "visitor_edit_inflight.json").symlink_to(actual)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    with pytest.raises(RuntimeError, match="arquivo regular"):
        select_edit_journal("1738", tmp_path)
