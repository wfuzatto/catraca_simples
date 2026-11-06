"""Field-level requirements for an editable visitor form; no network or Tk."""
from __future__ import annotations

from io import BytesIO
import re

from PIL import Image
from core import VALE_VISITOR_LEVELS, parse_visit_date, visitor_period_from_dates

CREDENTIAL_MODES = {"Somente foto": (True, False),
                    "Somente pulseira": (False, True),
                    "Foto e pulseira": (True, True)}
EDIT_OPERATIONS = {"Atualizar visita existente": "update", "Renovar visita": "renew"}
CLOSED_VISIT_STATES = {"check-out", "saída automática", "saída no terminal", "saída em atraso"}


def editor_requirements(record, visitor=None, *, photo_error="", photo_loaded=False):
    visitor = visitor if isinstance(visitor, dict) else {}
    photo_meta = visitor.get("visitorPhoto") or visitor.get("VisitorPhoto") or {}
    photo_meta = photo_meta if isinstance(photo_meta, dict) else {}
    face = bool(photo_loaded or record.get("expect_face") or record.get("photo_url") or
                visitor.get("faces") or photo_meta.get("haveVisitorPhoto"))
    card = bool(record.get("expect_card") or visitor.get("cards") or record.get("card_number"))
    mode = "Foto e pulseira" if face and card else (
        "Somente foto" if face else "Somente pulseira" if card else "")
    if photo_error and card:
        mode = "Foto e pulseira"
    known_operation = record.get("_edit_operation")
    known_state = record.get("checkin_state")
    return {"require_photo": bool(photo_error or face), "require_card": card,
            "initial_credential_mode": mode,
            "require_appointment": known_operation == "renew" or known_state in CLOSED_VISIT_STATES,
            "require_operation": known_operation not in ("update", "renew") and
                                 known_state not in CLOSED_VISIT_STATES | {"check-in efetuado"}}


def validate_editor_fields(values, settings, available_groups, *, photo_bytes=b"",
                           require_photo=False, require_card=False, now=None,
                           initial_credential_mode="", require_appointment=False,
                           require_operation=False):
    """Return actionable errors by field; optional protocol fields may be blank."""
    errors = {}
    text = {key: str(value or "").strip() for key, value in values.items()}
    if require_operation and text.get("operation") not in EDIT_OPERATIONS:
        errors["operation"] = "Selecione se deseja atualizar a visita existente ou renovar a visita."
    if require_appointment or text.get("operation") == "Renovar visita":
        appointment = text.get("appointment", "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", appointment):
            errors["appointment"] = "Selecione ou informe o ID da reserva vinculada no HikCentral."
    for key, label in (("first", "nome"), ("last", "sobrenome")):
        if len(text.get(key, "")) < 2:
            errors[key] = f"Informe o {label} (mínimo de 2 caracteres)."
        elif len(text[key]) > 64:
            errors[key] = f"O {label} deve ter no máximo 64 caracteres."
    if len(text.get("reservation", "")) > 255:
        errors["reservation"] = "Reserva/apartamento deve ter no máximo 255 caracteres."
    group = text.get("group", "")
    if not group or group not in available_groups or group not in VALE_VISITOR_LEVELS:
        errors["group"] = "Selecione explicitamente um grupo válido de visitantes."
    else:
        mapping = settings.get("group_access", {})
        if mapping.get(group) != [VALE_VISITOR_LEVELS[group]]:
            errors["group"] = "Configure o nível Visitor correspondente ao grupo selecionado."
    for key, label in (("entry", "entrada"), ("exit", "saída")):
        try:
            parse_visit_date(text.get(key, ""))
        except (ValueError, TypeError):
            errors[key] = f"Informe a data de {label} em DD/MM/AAAA."
    if "entry" not in errors and "exit" not in errors:
        try:
            visitor_period_from_dates(text["entry"], text["exit"], settings)
        except (ValueError, TypeError) as exc:
            errors["exit"] = str(exc)
    mode = text.get("credential_mode", "")
    mode_photo, mode_card = CREDENTIAL_MODES.get(mode, (False, False))
    if mode not in CREDENTIAL_MODES:
        errors["credential_mode"] = "Selecione como este visitante será identificado: foto, pulseira ou ambos."
    elif (require_photo and not mode_photo) or (require_card and not mode_card):
        errors["credential_mode"] = "Selecione uma modalidade que preserve as credenciais já indicadas no cadastro."
    card = text.get("card", "")
    if mode in CREDENTIAL_MODES and ((photo_bytes and not mode_photo) or (card and not mode_card)):
        errors["credential_mode"] = "Selecione uma modalidade que inclua a foto e/ou pulseira preenchidas."
    if card and not re.fullmatch(r"[A-Za-z0-9]{1,32}", card):
        errors["card"] = "A pulseira deve ter de 1 a 32 caracteres alfanuméricos."
    elif (require_card or mode_card) and not card:
        errors["card"] = "Reinforme a pulseira para preservar a credencial NFC."
    if (require_photo or mode_photo) and not photo_bytes:
        errors["photo"] = "Selecione uma foto ou use a câmera para completar o cadastro."
    elif photo_bytes:
        try:
            if len(photo_bytes) > 2_000_000:
                raise ValueError()
            with Image.open(BytesIO(photo_bytes)) as image:
                if image.format != "JPEG":
                    raise ValueError()
                image.verify()
        except Exception:
            errors["photo"] = "Selecione uma foto JPEG válida de até 2 MB."
    if not photo_bytes and not card:
        errors.setdefault("photo", "Selecione uma foto ou informe uma pulseira.")
        errors.setdefault("card", "Informe uma pulseira ou selecione uma foto.")
    return errors
