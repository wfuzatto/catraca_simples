"""Retomada segura de credenciais de uma reserva Visitor já identificada.

Nenhum POST de reserva/check-in é feito aqui. O contexto do operador é salvo
com DPAPI do Windows, nunca com cartão, nome ou foto em JSON legível.
"""
from __future__ import annotations

import base64
import binascii
import json

from hikcentral import HikError, normalize
from remote_history import find_original_appointment, _appointment_visitor_info, compare_reservation_period

CONTEXT_PREFIX = "dpapi:v1:"


def seal_registration_context(*, first: str, last: str, group: str,
                              card: str, photo: bytes = b"") -> str:
    """Criptografa dados de recuperação com a identidade do usuário Windows."""
    if not first.strip() or not last.strip() or not group.strip():
        raise ValueError("Identificação incompleta para recuperação automática.")
    import win32crypt
    data = {
        "version": 1, "first": first.strip(), "last": last.strip(),
        "group": group.strip(), "card": card.strip(),
        "photo": base64.b64encode(photo).decode("ascii"),
    }
    cleartext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    ciphertext = win32crypt.CryptProtectData(
        cleartext, "Vale Visitor - recuperação de cadastro", None, None, None, 0)
    return CONTEXT_PREFIX + base64.b64encode(ciphertext).decode("ascii")


def open_registration_context(value: str) -> dict:
    """Aceita somente contexto DPAPI íntegro; não usa fallback em texto aberto."""
    if not isinstance(value, str) or not value.startswith(CONTEXT_PREFIX):
        raise ValueError("Credenciais antigas sem contexto protegido; requer revisão.")
    try:
        ciphertext = base64.b64decode(value[len(CONTEXT_PREFIX):], validate=True)
        import win32crypt
        _, cleartext = win32crypt.CryptUnprotectData(
            ciphertext, None, None, None, 0)
        data = json.loads(cleartext.decode("utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("Versão inválida do contexto protegido.")
        if any(not isinstance(data.get(key), str) for key in
               ("first", "last", "group", "card", "photo")):
            raise ValueError("Campos inválidos no contexto protegido.")
        if not data["first"].strip() or not data["last"].strip() or not data["group"].strip():
            raise ValueError("Identidade incompleta no contexto protegido.")
        # Decodificar aqui para validar a foto antes de uma atualização remota.
        data["photo_bytes"] = base64.b64decode(data["photo"], validate=True)
        return data
    except (binascii.Error, UnicodeError, json.JSONDecodeError, TypeError,
            OSError, ValueError) as exc:
        raise ValueError(
            "Não foi possível recuperar o contexto protegido neste usuário Windows.") from exc


def verify_pending_reservation(client, pending: dict, context: dict) -> None:
    """Confere a reserva original, Visitor ID, período e identidade antes de editar.

    Uma divergência significa potencial visitante errado: não altera a pessoa.
    """
    visitor_id = str(pending.get("visitor_id") or "")
    appointment_id = str(pending.get("appointment_id") or "")
    start = str(pending.get("entry_at") or "")
    end = str(pending.get("exit_at") or "")
    if not all((visitor_id, appointment_id, start, end)):
        raise HikError("Pendência sem IDs/período suficientes para conferir a reserva.")
    appointment = find_original_appointment(client, {
        "visitor_id": visitor_id, "appointment_id": appointment_id,
        "entry_at": start, "exit_at": end,
    })
    period = compare_reservation_period(appointment, start, end)
    if period["appointment_id"] != appointment_id or period["state"] != "matched":
        raise HikError("Reserva/período divergente; nenhuma credencial foi alterada.")
    info = _appointment_visitor_info(appointment)
    first = str(info.get("visitorGivenName") or info.get("firstName") or "").strip()
    last = str(info.get("visitorFamilyName") or info.get("lastName") or "").strip()
    if not first or not last or (
        normalize(first), normalize(last)
    ) != (normalize(context["first"]), normalize(context["last"])):
        raise HikError(
            "Identidade da reserva não corresponde ao cadastro pendente. "
            "Revisão humana necessária.")
    group = str(info.get("visitorGroupName") or info.get("visitorGroup") or "").strip()
    if group and normalize(group) != normalize(context["group"]):
        raise HikError("Grupo da reserva divergente; revisão humana necessária.")


def restore_pending_credentials(client, pending: dict, context: dict) -> bool:
    """Concilia perfil e pulseira do MESMO Visitor ID. Não cria outro registro."""
    from visitor_profile import update_profile, confirm_checkin_card

    verify_pending_reservation(client, pending, context)
    visitor_id = str(pending["visitor_id"])
    card = context["card"] if pending.get("expect_card", False) else ""
    if pending.get("expect_card", False) and not card:
        raise HikError("A pulseira original não está no contexto de recuperação.")
    # update_profile compara as informações remotas e só altera campos divergentes.
    # A foto já pode ter sido enviada na reserva; atualizar apenas se necessário.
    update_profile(client, visitor_id, context["first"], context["last"],
                   card=card, group=context["group"],
                   new_photo=context.get("photo_bytes", b""))
    if card and pending.get("checkin_requested", True):
        if not pending.get("registered") or not pending.get("registration_record_id"):
            raise HikError("Ainda não existe check-in confirmado para conferir a pulseira.")
        confirm_checkin_card(client, visitor_id, pending["entry_at"],
                             pending["exit_at"], pending["registration_record_id"], card)
    return True
