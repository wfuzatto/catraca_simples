"""Safely clear only *active local transaction journals* after operator review.

Never delete application settings, API keys, visitor history, downloaded face
photos or any unknown files. Original journal bytes move to audit directory.
No HikCentral request is performed here and nothing is resent.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import os
import stat

from core import TZ, app_dir

TRANSACTION_FILES = (
    "pending.json",
    "visitor_edit_inflight.json",
    "last_delivery.json",
)


def inspect_transaction_files(base: Path | None = None) -> list[dict]:
    base = base if base is not None else app_dir()
    result = []
    for filename in TRANSACTION_FILES:
        path = base / filename
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(
                f"{filename} não é um arquivo regular; limpeza cancelada.")
        raw = path.read_bytes()
        result.append({
            "name": filename,
            "size": len(raw),
            "sha256": sha256(raw).hexdigest(),
        })
    return result


def archive_transaction_files(snapshot: list[dict], confirmation: str,
                              base: Path | None = None) -> list[Path]:
    """Release journals only with explicit ACK and matching current hashes.

    Archive under the same user profile, no cross-filesystem copy and no
    arbitrary wildcard deletion. Reject modified files before changing any.
    """
    if confirmation.strip() != "CONFERIDO":
        raise ValueError(
            "Confira pendências no HikCentral e digite CONFERIDO para liberar.")
    base = base if base is not None else app_dir()
    current = inspect_transaction_files(base)
    if current != snapshot:
        raise RuntimeError(
            "Os arquivos de transação mudaram durante a conferência. "
            "Reabra a janela; nenhum arquivo foi liberado.")
    if not current:
        return []
    audit = base / "transaction_audit"
    audit.mkdir(parents=True, exist_ok=True)
    if audit.is_symlink():
        raise RuntimeError("Pasta de auditoria inválida.")
    prefix = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    transaction_id = uuid4().hex[:10]
    destinations = [
        audit / f"{prefix}_{transaction_id}_{item['name']}"
        for item in current
    ]
    if any(path.exists() for path in destinations):
        raise RuntimeError("Arquivos de auditoria já existem. Refaça a operação.")
    archived = []
    for item, destination in zip(current, destinations):
        path = base / item["name"]
        # A later writer could change the file after inspection. Check again
        # immediately before each move; never follow symlinks.
        if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(
                f"{item['name']} mudou durante a limpeza. "
                "Os arquivos já arquivados permanecem preservados.")
        os.replace(path, destination)
        archived.append(destination)
    return archived
