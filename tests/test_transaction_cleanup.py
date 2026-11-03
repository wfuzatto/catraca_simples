"""Transaction cleanup is a narrow, auditable local journal operation."""
from pathlib import Path
import json

import pytest

from transaction_cleanup import (
    TRANSACTION_FILES, inspect_transaction_files, archive_transaction_files
)


def write_files(base):
    base.mkdir(parents=True, exist_ok=True)
    for name in TRANSACTION_FILES:
        (base / name).write_text('{"stage":"interrupted"}', encoding="utf-8")
    # Cache clearing must never touch these or random files.
    (base / "settings.json").write_text('{"host":"192.168.1.100"}', encoding="utf-8")
    (base / "settings.backup-pre-visitor-levels.json").write_text(
        '{"previous":true}', encoding="utf-8")
    (base / "secrets.dpapi").write_bytes(b"encrypted-secret-data")
    (base / "visitor_history.dpapi").write_bytes(b"encrypted-history-data")
    (base / "unrelated.txt").write_text("do not touch", encoding="utf-8")


def test_only_whitelisted_journals_archived_and_original_bytes_preserved(tmp_path):
    write_files(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    snapshot = inspect_transaction_files(tmp_path)
    assert {x["name"] for x in snapshot} == set(TRANSACTION_FILES)
    with pytest.raises(ValueError, match="CONFERIDO"):
        archive_transaction_files(snapshot, "CANCELAR", tmp_path)
    assert all((tmp_path / name).exists() for name in TRANSACTION_FILES)
    archived = archive_transaction_files(snapshot, "CONFERIDO", tmp_path)
    assert len(archived) == len(TRANSACTION_FILES)
    for p in archived:
        filename = next(name for name in TRANSACTION_FILES
                        if p.name.endswith("_" + name))
        assert p.read_bytes() == before[filename]
        assert not (tmp_path / filename).exists()
    for name in before:
        if name not in TRANSACTION_FILES:
            assert (tmp_path / name).read_bytes() == before[name]


def test_stale_snapshot_does_not_clear_anything(tmp_path):
    write_files(tmp_path)
    before = inspect_transaction_files(tmp_path)
    (tmp_path / "pending.json").write_text('{"stage":"changed"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="mudaram"):
        archive_transaction_files(before, "CONFERIDO", tmp_path)
    assert all((tmp_path / name).exists() for name in TRANSACTION_FILES)
    assert not (tmp_path / "transaction_audit").exists()


def test_empty_cache_keeps_settings_and_history(tmp_path):
    (tmp_path / "settings.json").write_text("settings", encoding="utf-8")
    (tmp_path / "visitor_history.dpapi").write_bytes(b"history")
    assert inspect_transaction_files(tmp_path) == []
    assert archive_transaction_files([], "CONFERIDO", tmp_path) == []
    assert (tmp_path / "settings.json").read_text(encoding="utf-8") == "settings"
    assert (tmp_path / "visitor_history.dpapi").read_bytes() == b"history"


def test_rejects_symlink_to_configuration_file(tmp_path):
    config = tmp_path / "settings.json"
    config.write_text('{"keep":true}', encoding="utf-8")
    link = tmp_path / "pending.json"
    try:
        link.symlink_to(config)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not available in this environment.")
    with pytest.raises(RuntimeError, match="regular"):
        inspect_transaction_files(tmp_path)
    assert config.read_text(encoding="utf-8") == '{"keep":true}'


def test_cleanup_always_preserves_previous_audit_snapshots(tmp_path):
    (tmp_path / "pending.json").write_text("first", encoding="utf-8")
    first = archive_transaction_files(
        inspect_transaction_files(tmp_path), "CONFERIDO", tmp_path)
    (tmp_path / "pending.json").write_text("second", encoding="utf-8")
    second = archive_transaction_files(
        inspect_transaction_files(tmp_path), "CONFERIDO", tmp_path)
    assert len(first) == len(second) == 1
    assert first[0] != second[0]
    assert first[0].read_text(encoding="utf-8") == "first"
    assert second[0].read_text(encoding="utf-8") == "second"
