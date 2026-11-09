"""Emissão de NFC no registro de Visitor, não apenas em Person.cards.

A OpenAPI de Visitor aceita cards[] no VisitorInfo da atualização da reserva.
Uma ficha Person com cartão não é prova de credencial Visitor emitida.

Não cria visitantes, check-ins ou reservas. Faz uma única atualização na
reserva existente com ID, código, período e nível de acesso conferidos.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

from core import _write_json_atomic
from edit_errors import EditorFieldError
from edit_journal import select_edit_journal
from hikcentral import HikError, normalize
from ordered_reservation_change import _lock_transaction, _write_once
from remote_history import compare_reservation_period, visitor_details
from reservation_replacement import _booking_by_code
from unregistered_reservation import UPDATE_BOOKING, _period, _request
from visitor_profile import set_visitor_group, validate_visitor_remark
from visitor_remark import _original_group_from_level


def _card_values(info):
    if not isinstance(info, dict) or "cards" not in info:
        return None  # API omitiu o campo: não é prova de emissão.
    cards = info["cards"]
    if not isinstance(cards, list):
        raise EditorFieldError({"card": "HikCentral retornou cards em formato inválido."})
    values = []
    for item in cards:
        if not isinstance(item, dict) or not str(item.get("cardNo") or "").strip():
            raise EditorFieldError({"card": "HikCentral retornou um cartão sem cardNo."})
        values.append(str(item["cardNo"]).strip())
    if len(values) > 1:
        raise EditorFieldError({"card": "Há mais de uma credencial neste visitante. Revise o cadastro."})
    return tuple(values)


def _confirmed_card(client, vid, booking, card, remark):
    profile = visitor_details(client, vid)
    info = profile.get("VisitorInfo") or profile.get("visitorInfo") or profile
    if not isinstance(info, dict) or str(info.get("visitorId") or "") != vid:
        raise EditorFieldError({"card": "Visitor Information retornou outro visitante."})
    booking_info = booking.get("visitorInfo") or {}
    if str(booking_info.get("visitorId") or "") != vid:
        raise EditorFieldError({"card": "A reserva foi associada a outro visitante."})
    # O Person pode devolver cards[] sem que o Visitante tenha recebido cartão.
    # Não tratar a ficha Person como prova de emissão.
    remote_card = _card_values(info)
    booked_card = _card_values(booking_info)
    verified = remote_card == (card,) and booked_card == (card,)
    remark_ok = (str(info.get("remark") or "").strip() == remark and
                 str(booking_info.get("remark") or "").strip() == remark)
    return verified and remark_ok, remote_card, booked_card


def update_reserved_visitor_card(client, record, card, expected_group, *, remark=None,
                                 journal=None):
    """Concilia NFC no VisitorInfo da reserva original SEM reenvio inseguro.

    A reserva só pode ser atualizada quando ainda não tem check-in. Se o envio
    remoto ficar incerto, o journal impede outro POST até a conciliação.
    """
    vid = str(record.get("visitor_id") or "").strip()
    aid = str(record.get("appointment_id") or "").strip()
    code = str(record.get("reservation_code") or "").strip()
    card = str(card or "").strip()
    expected_group = str(expected_group or "").strip()
    if not re.fullmatch(r"[0-9]{1,48}", vid) or not re.fullmatch(r"[0-9]{1,64}", aid):
        raise EditorFieldError({"card": "Visitor ID e ID da reserva precisam ser confirmados."})
    if not re.fullmatch(r"[0-9]{1,8}", code):
        raise EditorFieldError({"card": "Código de reserva ausente. Atualize o histórico antes de emitir o NFC."})
    if not re.fullmatch(r"[A-Za-z0-9]{1,32}", card):
        raise EditorFieldError({"card": "Informe de 1 a 32 letras ou números para a pulseira."})
    start, end = _period(record.get("reservation_entry_at") or record.get("entry_at"),
                         record.get("reservation_exit_at") or record.get("exit_at"))
    journal = journal or select_edit_journal("visitor_nfc:" + vid + ":" + code)
    scope = {
        "server": str(getattr(client, "url", "")), "visitor_id": vid,
        "code": code, "entry": start, "exit": end,
        "group": expected_group, "card_digest": sha256(card.encode()).hexdigest(),
    }

    with _lock_transaction(journal):
        booking = _booking_by_code(client, code, start, end, vid)
        if not booking or str(booking.get("appointStatus")) != "0":
            raise EditorFieldError({"card": (
                "Reserva não encontrada ou já registrada. Atualize o histórico e edite "
                "a credencial pelo check-in existente.")})
        period = compare_reservation_period(booking, start, end)
        if period["state"] != "matched":
            raise EditorFieldError({"card": "Período da reserva mudou; confira antes de emitir o cartão."})
        # The appointment may rotate its ID after a previous edit. Only
        # accept the original ID for a FIRST submission; a pending journal
        # may be reconciled by the same unique code+visitor+period afterward.
        if not journal.exists() and str(period["appointment_id"]) != aid:
            raise EditorFieldError({"card": (
                "O ID da reserva mudou desde a consulta. Recarregue o histórico antes de gravar NFC.")})
        if client.find_registration_record(vid, start, end):
            raise EditorFieldError({"card": (
                "Já existe check-in para esse período. Não atualizar a reserva; "
                "atualize a credencial no check-in.")})
        group = _original_group_from_level(booking, record)
        if not group or normalize(group) != normalize(expected_group):
            raise EditorFieldError({"group": (
                "Grupo/nivel da reserva não corresponde ao grupo do seletor. "
                "Atualize o histórico antes de emitir NFC.")})
        current_profile = visitor_details(client, vid)
        profile = (current_profile.get("VisitorInfo") or current_profile.get("visitorInfo")
                   or current_profile)
        if not isinstance(profile, dict) or str(profile.get("visitorId") or "") != vid:
            raise EditorFieldError({"card": "Visitor Info não confirmou a identidade do visitante."})
        wanted_remark = validate_visitor_remark(
            str(profile.get("remark") or "") if remark is None else str(remark))
        existing_card = _card_values(profile)
        if existing_card is not None and existing_card not in ((), (card,)):
            # A substituição só é permitida para um cartão e visitante selecionados.
            if not str(record.get("card_number") or "") and not journal.exists():
                raise EditorFieldError({"card": (
                    "Visitor possui cartão diferente. Recarregue os dados antes de substituir.")})

        if journal.exists():
            try:
                pending = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise HikError("Journal de emissão NFC não está legível; nenhum reenvio.") from exc
            if not isinstance(pending, dict) or any(pending.get(k) != v for k,v in scope.items()):
                raise EditorFieldError({"card": "Outra emissão NFC pendente nesta reserva; confira antes de repetir."})
            confirmed, remote_card, booked_card = _confirmed_card(
                client, vid, booking, card, wanted_remark)
            if not confirmed:
                raise EditorFieldError({"card": (
                    "A emissão anterior não foi confirmada nas APIs de Visitor e reserva. "
                    "Não reenviar automaticamente; confira Credential Management.")})
            set_visitor_group(client, vid, group)
            journal.unlink()
            return True

        confirmed, _, _ = _confirmed_card(client, vid, booking, card, wanted_remark)
        if confirmed:
            set_visitor_group(client, vid, group)
            return False

        # A API de reserva recebe o cartão pelo VisitorInfo.cards[] e mantém
        # o nível original; Person.cards[] sozinho não é suficiente.
        body = _request(client, booking, vid, start, end, wanted_remark, group,
                        None, issued_card=card)
        # Evitar que uma edição simultânea do nome em Person restaure nome
        # antigo da reserva, cujo conteúdo pode estar desatualizado.
        outbound = body["visitorInfoList"][0]["VisitorInfo"]
        for field in ("visitorGivenName", "visitorFamilyName"):
            if profile.get(field):
                outbound[field] = profile[field]
        _write_json_atomic(journal, scope)
        _, write_error = _write_once(lambda: client.post(UPDATE_BOOKING, body))
        updated = _booking_by_code(client, code, start, end, vid)
        if not updated:
            raise EditorFieldError({"card": (
                "HikCentral não confirmou a mesma reserva após enviar NFC. "
                "Envio bloqueado até conciliar.")}) from write_error
        observed, _, _ = _confirmed_card(client, vid, updated, card, wanted_remark)
        if not observed:
            raise EditorFieldError({"card": (
                "O HikCentral recebeu a atualização da reserva, mas não confirmou "
                "cards[] no Visitor Information E na reserva. Não considerar a pulseira emitida.")}) from write_error
        set_visitor_group(client, vid, group)
        journal.unlink()
        return True
