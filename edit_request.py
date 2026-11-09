"""Validate a complete edit before any network mutation."""
from copy import deepcopy
from datetime import datetime
import re
from edit_errors import EditorFieldError


def separate_card_from_document(info, card_numbers=None):
    """Never copy an NFC card number into the visitor's ID document field."""
    if not isinstance(info, dict):
        return False
    document_no = str(info.get("certificateNo") or "").strip()
    if not document_no:
        return False
    if card_numbers is None:
        cards = info.get("cards") or []
        card_numbers = [card.get("cardNo") for card in cards if isinstance(card, dict)] \
            if isinstance(cards, list) else []
    known_cards = {str(value or "").strip().casefold() for value in card_numbers if str(value or "").strip()}
    if document_no.casefold() not in known_cards:
        return False
    info["certificateNo"] = ""
    return True


def validate_edit_request(payload, *, require_credential=True):
    errors = {}
    entries = payload.get("visitorInfoList") or []
    info = entries[0].get("VisitorInfo", {}) if len(entries) == 1 and isinstance(entries[0], dict) else {}
    for key, field, label in (("visitorGivenName", "first", "nome"),
                              ("visitorFamilyName", "last", "sobrenome")):
        if not 2 <= len(str(info.get(key) or "").strip()) <= 64:
            errors[field] = f"Informe {label} com 2 a 64 caracteres."
    if not str(info.get("visitorGroupName") or "").strip():
        errors["group"] = "Selecione o grupo de visitantes."
    times = []
    for key, field in (("visitStartTime", "entry"), ("visitEndTime", "exit")):
        try:
            value = datetime.fromisoformat(payload[key])
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError()
            times.append(value)
        except (KeyError, ValueError, TypeError):
            errors[field] = "Informe uma data válida com o horário configurado."
    if len(times) == 2 and times[1] <= times[0]:
        errors["exit"] = "A saída deve ser posterior à entrada."
    cards, faces = info.get("cards") or [], info.get("faces") or []
    if require_credential and not cards and not faces:
        errors["credential"] = "Selecione foto, pulseira ou ambas e complete a credencial."
    if cards and (not isinstance(cards, list) or any(
            not isinstance(x, dict) or not re.fullmatch(r"[A-Za-z0-9]{1,32}", str(x.get("cardNo") or ""))
            for x in cards)):
        errors["card"] = "Informe a pulseira com 1 a 32 caracteres alfanuméricos."
    if faces and (not isinstance(faces, list) or any(
            not isinstance(x, dict) or not x.get("faceData") for x in faces)):
        errors["photo"] = "Selecione ou capture uma foto válida."
    if errors:
        raise EditorFieldError(errors)


def preserve_visitor_metadata(payload, details):
    """Keep known fields that this form does not edit; never copy image URLs."""
    result = deepcopy(payload)
    original = details.get("VisitorInfo") or details.get("visitorInfo") or details
    info = result["visitorInfoList"][0]["VisitorInfo"]
    if isinstance(original, dict):
        for key in ("gender", "companyName", "certificateType", "certificateNo",
                    "email", "phoneNo", "plateNo", "customFieldList", "watchListInfo"):
            if key in original and original[key] is not None:
                info[key] = deepcopy(original[key])
    info.setdefault("gender", 0)
    if not info.get("watchListInfo"):
        info["watchListInfo"] = [{"enableRegister": 1, "ID": "0", "Type": "1"}]
    separate_card_from_document(info)
    return result
