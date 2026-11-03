"""Evidence of a previously ACKNOWLEDGED access-level assignment.

Stored ONLY inside visitor_history.dpapi, encrypted to the Windows user.
This is local evidence of what our application last applied, NOT proof that
some other station has not changed the visitor's grants since that operation.
Never use this evidence if server-provided access levels conflict.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

MAX_EVIDENCE_AGE = timedelta(days=7)


def _when(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result if result.tzinfo else None


def make_access_receipt(record, *, level_id, door_ids, confirmed_at=None):
    """Metadata after update ACK + successful delivery + readback identity check."""
    moment = confirmed_at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Horário de confirmação requer fuso.")
    if not level_id or not door_ids or len(set(door_ids)) != len(door_ids):
        raise ValueError("IDs de nível e portas obrigatórios.")
    required = ("visitor_id", "registration_record_id", "group",
                "entry_at", "exit_at")
    if any(not record.get(key) for key in required):
        raise ValueError("Registro incompleto para evidência local.")
    names = record.get("access_names") or []
    if len(names) != 1:
        raise ValueError("Acesso deve conter exatamente um nível Visitor.")
    return {
        "version": 1,
        "source": "acknowledged_update_and_confirmed_delivery",
        "confirmed_at": moment.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "visitor_id": str(record["visitor_id"]),
        "record_id": str(record["registration_record_id"]),
        "group": str(record["group"]),
        "entry_at": str(record["entry_at"]),
        "exit_at": str(record["exit_at"]),
        "level_id": str(level_id),
        "level_name": str(names[0]),
        "door_ids": sorted(str(x) for x in door_ids),
    }


def matching_access_receipt(receipt, record, *, level_id, level_name,
                            door_ids, now=None):
    """Conservatively recognize a recent, identical local confirmed assignment.

    This does NOT remotely verify membership. It only avoids asking the same
    operator to reauthorize the SAME already-acknowledged access plan on
    the SAME computer/user within seven days.
    """
    if not isinstance(receipt, dict):
        return False
    if receipt.get("version") != 1 or receipt.get("source") != (
            "acknowledged_update_and_confirmed_delivery"):
        return False
    timestamp = _when(receipt.get("confirmed_at"))
    current = now or datetime.now(timezone.utc)
    if not timestamp or current.tzinfo is None:
        return False
    age = current.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)
    if age < timedelta(0) or age > MAX_EVIDENCE_AGE:
        return False
    fields = (
        ("visitor_id", "visitor_id"),
        ("record_id", "registration_record_id"),
        ("group", "group"),
    )
    if any(str(receipt.get(a) or "") != str(record.get(b) or "")
           for a, b in fields):
        return False
    for field in ("entry_at", "exit_at"):
        original = _when(receipt.get(field))
        remote = _when(record.get(field))
        if original is None or remote is None or original != remote:
            return False
    if str(receipt.get("level_id") or "") != str(level_id):
        return False
    if str(receipt.get("level_name") or "") != str(level_name):
        return False
    saved_doors = receipt.get("door_ids")
    current_doors = [str(x) for x in door_ids]
    if (not isinstance(saved_doors, list) or
            len(saved_doors) != len(current_doors) or
            sorted(saved_doors) != sorted(current_doors) or
            len(set(saved_doors)) != len(saved_doors)):
        return False
    return True
