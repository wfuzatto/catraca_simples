"""Keep uncertain visitor edits isolated by visitor, including legacy journals."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import stat

from core import app_dir


def _plain_directory(path: Path) -> None:
    """Do not follow a replaced journal/audit directory to another location."""
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise RuntimeError("Pasta de transações inválida; nenhuma edição autorizada.")
    if path.exists() and not path.is_dir():
        raise RuntimeError("Pasta de transações inválida; nenhuma edição autorizada.")


def select_edit_journal(visitor_id: str, base: Path | None = None) -> Path:
    """Select this visitor's journal without discarding another pending edit.

    Old versions wrote one global file. Keep consulting that file for its
    original visitor, while unrelated visitors get independent journals.
    """
    visitor_id = str(visitor_id or "").strip()
    if not visitor_id or len(visitor_id) > 64:
        raise ValueError("Visitor ID inválido para registrar a edição.")
    base = base if base is not None else app_dir()
    legacy = base / "visitor_edit_inflight.json"
    try:
        metadata = legacy.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Registro de edição pendente não é um arquivo regular.")
        try:
            pending = json.loads(legacy.read_text(encoding="utf-8"))
            if not isinstance(pending, dict) or not isinstance(pending.get("visitor_id"), str):
                raise ValueError("Visitor ID inválido")
            pending_id = str(pending.get("visitor_id") or "").strip()
            if not pending_id or len(pending_id) > 64:
                raise ValueError("Visitor ID ausente")
        except (ValueError, OSError, AttributeError) as exc:
            raise RuntimeError(
                "Registro antigo de edição pendente inválido; confira a pendência "
                "antes de liberar alterações.") from exc
        if pending_id == visitor_id:
            return legacy

    directory = base / "visitor_edits"
    _plain_directory(directory)
    directory.mkdir(parents=True, exist_ok=True)
    _plain_directory(directory)
    filename = sha256(visitor_id.encode("utf-8")).hexdigest() + ".json"
    path = directory / filename
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return path
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("Registro de edição pendente não é um arquivo regular.")
    return path
