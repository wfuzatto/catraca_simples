"""Diagnóstico local de uma operação Visitor interrompida.

Nenhuma das funções neste módulo acessa o HikCentral ou faz novo cadastro.
A liberação manual PRESERVA o journal em arquivo de auditoria e requer
revisão humana e confirmação explícita.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import os

from core import TZ, app_dir

PENDING_STAGE_DESCRIPTIONS = {
    "appointment_inflight": (
        "Reserva enviada ou prestes a ser enviada; resultado remoto incerto. "
        "Ainda não existe Visitor ID nesta etapa. Não reenviar automaticamente."
    ),
    "appointment_confirmed": (
        "Reserva já retornou IDs ao aplicativo. Check-in, níveis ou entrega "
        "podem ainda estar pendentes; a conciliação tentará localizar o registro "
        "existente antes de continuar."
    ),
}


def read_pending_diagnostics(base: Path | None = None) -> dict | None:
    """Extrai apenas metadados úteis, sem carregar fotos, cartão ou credenciais."""
    path = (base or app_dir()) / "pending.json"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "O journal pending.json está ilegível; preserve o arquivo para auditoria."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError("A pendência tem formato inválido; preserve o arquivo.")
    stage = str(data.get("stage") or "desconhecida")
    stamp = datetime.fromtimestamp(path.stat().st_mtime, TZ)
    visitor_id = str(data.get("visitor_id") or "").strip()
    appointment_id = str(data.get("appointment_id") or "").strip()
    record_id = str(data.get("registration_record_id") or "").strip()
    can_resume = bool(
        stage == "appointment_confirmed" and visitor_id and appointment_id
        and isinstance(data.get("door_ids"), list) and data.get("door_ids")
        and isinstance(data.get("access_names"), list) and len(data.get("access_names")) == 1
        and data.get("entry_at") and data.get("exit_at")
    )
    return {
        "stage": stage, "description": PENDING_STAGE_DESCRIPTIONS.get(
            stage, "Etapa não reconhecida; nenhuma operação deve ser reenviada automaticamente."),
        "visitor_id": visitor_id, "appointment_id": appointment_id,
        "registration_record_id": record_id,
        "registered": bool(data.get("registered", False)),
        "entry_at": str(data.get("entry_at") or ""),
        "exit_at": str(data.get("exit_at") or ""),
        "access_names": [str(x) for x in data.get("access_names", [])]
        if isinstance(data.get("access_names"), list) else [],
        "assigned_count": len(data.get("assigned", []))
        if isinstance(data.get("assigned"), list) else 0,
        "door_count": len(data.get("door_ids", []))
        if isinstance(data.get("door_ids"), list) else 0,
        "modified_at": stamp.strftime("%d/%m/%Y %H:%M:%S"),
        "sha256": sha256(raw).hexdigest(),
        "can_resume": can_resume,
        "path": str(path),
    }


def archive_pending_after_manual_review(expected_sha256: str, confirmation: str,
                                        base: Path | None = None) -> Path:
    """Libera estação só após revisão declarada; NÃO envia nada ao HikCentral.

    Hash impede liberar uma pendência modificada durante a revisão. O arquivo
    original é movido para uma cópia local de auditoria (não é apagado).
    """
    if confirmation.strip() != "CONFERIDO":
        raise ValueError("Digite CONFERIDO após revisar o registro no HikCentral.")
    base = base or app_dir()
    path = base / "pending.json"
    if not path.is_file():
        raise RuntimeError("Não há pendência ativa para arquivar.")
    if not expected_sha256 or len(expected_sha256) != 64:
        raise ValueError("Identificação da pendência inválida.")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError("A operação pendente mudou durante a conferência. Reabra a janela.")
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    dest = base / f"pending_reviewed_{stamp}_{uuid4().hex[:8]}.json"
    if dest.exists():
        raise RuntimeError("Nome de arquivo de auditoria já utilizado; tente novamente.")
    os.rename(path, dest)
    return dest
