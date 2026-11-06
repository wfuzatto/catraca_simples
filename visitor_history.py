"""Encrypted local Visitor history. Windows DPAPI binds it to the current profile.

No raw cards, facial images or credentials are stored in this history.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from core import app_dir

def _path(base=None):
    return (base or app_dir()) / "visitor_history.dpapi"

def _read(base=None):
    path = _path(base)
    if not path.exists():
        return []
    if os.name != "nt":
        raise RuntimeError("Histórico protegido exige Windows.")
    import win32crypt
    try:
        plaintext = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1]
        records = json.loads(plaintext.decode("utf-8"))
        if not isinstance(records, list):
            raise ValueError("Histórico inválido")
        return records
    except Exception as exc:
        raise RuntimeError("Não foi possível abrir o histórico criptografado deste usuário Windows.") from exc

def _write(records, base=None):
    if os.name != "nt":
        raise RuntimeError("Histórico protegido exige Windows.")
    import win32crypt
    path = _path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(records, ensure_ascii=False).encode("utf-8")
    encrypted = win32crypt.CryptProtectData(data, "ValeVisitorHistory", None, None, None, 0)
    temp = path.with_suffix(".tmp")
    temp.write_bytes(encrypted)
    os.replace(temp, path)

def list_history(base=None):
    return list(reversed(_read(base)))

def save_registration(record, base=None):
    """Persist registration metadata; never save face, card, or passwords."""
    required = ("visitor_id", "door_ids", "entry_at", "exit_at", "access_names")
    if any(not record.get(field) for field in required):
        raise ValueError("Cadastro sem dados suficientes para o histórico.")
    allowed = ("visitor_id", "appointment_id", "registration_record_id",
               "first", "last", "group", "reservation",
               "door_ids", "access_names", "entry_at", "exit_at",
               "registered_at", "expect_face", "expect_card", "state", "checkin_state",
               "access_receipt")
    clean = {key: record[key] for key in allowed if key in record}
    clean["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = _read(base)
    for i, old in enumerate(records):
        if old["visitor_id"] == clean["visitor_id"]:
            # Never overwrite a confirmed server check-in with an older
            # snapshot from the initial registration attempt.
            confirmed_states = {"confirmado", "check-in efetuado"}
            if (old.get("checkin_state") in confirmed_states and
                    clean.get("checkin_state") not in confirmed_states):
                clean.pop("checkin_state", None)
            # A prova de acesso pertence SOMENTE ao mesmo check-in,
            # período e grupo. Não herdar o aceite para outra estadia.
            protected_fields = ("registration_record_id", "entry_at",
                                "exit_at", "group")
            if any(key in clean and str(clean[key]) != str(old.get(key))
                   for key in protected_fields):
                old.pop("access_receipt", None)
            records[i].update(clean)
            break
    else:
        records.append(clean)
    _write(records, base)

def set_history_status(visitor_id, *, state=None, checkin_state=None, base=None):
    rows = _read(base)
    for row in rows:
        if row["visitor_id"] == visitor_id:
            if state is not None:
                row["state"] = state
            if checkin_state is not None:
                row["checkin_state"] = checkin_state
            row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            _write(rows, base)
            return
    raise ValueError("Cadastro não encontrado no histórico local.")

def get_history(visitor_id, base=None):
    return next((item for item in _read(base) if item["visitor_id"] == visitor_id), None)
