"""Verificador da senha de acesso à aba Configurações.

O repositório contém somente sal e hash PBKDF2; nunca a senha em texto puro.
Proteção de interface local, não substitui permissões do Windows.
"""
from __future__ import annotations

import hashlib
import hmac

_SALT = bytes.fromhex("ccb7ba65fe096955f0a48cab8682732b")
_EXPECTED = bytes.fromhex("1c6b8047bb440eeb7493698802131cfb767f117c1ce3a9313b3498a96661a0b3")
_ITERATIONS = 390_000


def verify_settings_password(candidate: str | None) -> bool:
    if not candidate or not isinstance(candidate, str):
        return False
    candidate_hash = hashlib.pbkdf2_hmac(
        "sha256", candidate.encode("utf-8"), _SALT, _ITERATIONS)
    return hmac.compare_digest(candidate_hash, _EXPECTED)
