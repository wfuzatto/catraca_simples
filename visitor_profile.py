"""Edit the existing Visitor Information profile through OpenAPI."""
import base64
from io import BytesIO
import re

from PIL import Image, ImageChops, ImageStat

from core import prepare_face_jpeg
from edit_errors import EditorFieldError
from hikcentral import HikError
from remote_history import fetch_visitor_photo, visitor_details


PERSON_INFO = "/artemis/api/resource/v1/person/personId/personInfo"
PERSON_UPDATE = "/artemis/api/resource/v1/person/single/update"
FACE_UPDATE = "/artemis/api/resource/v1/person/face/update"


def validate_visitor_remark(value):
    """Return the remark accepted by Visitor Information."""
    remark = str(value or "").strip()
    if len(remark) > 128:
        raise EditorFieldError({"remark": "Informe até 128 caracteres no Remark do visitante."})
    return remark


def read_visitor_remark(client, visitor_id):
    """Read Visitor Remark, which differs from the Person API's remark field."""
    details = visitor_details(client, visitor_id)
    visitor = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if not isinstance(visitor, dict) or str(visitor.get("visitorId")) != visitor_id:
        raise HikError("O HikCentral não confirmou o Visitor ID ao consultar o apartamento.")
    return str(visitor.get("remark") or "").strip()


def read_profile(client, visitor_id, *, details=None):
    details = details if isinstance(details, dict) else visitor_details(client, visitor_id)
    visitor = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if not isinstance(visitor, dict):
        raise HikError("Visitor Information retornou um perfil em formato inesperado.")
    response = client.post(PERSON_INFO, {"personId": visitor_id})
    person = response.get("data") if isinstance(response, dict) else None
    if not isinstance(person, dict):
        raise HikError("A consulta de pessoa não retornou dados utilizáveis.")
    if str(visitor.get("visitorId")) != visitor_id or str(person.get("personId")) != visitor_id:
        raise HikError("O HikCentral não confirmou o mesmo Visitor ID nas duas consultas.")
    names = (str(visitor.get("visitorGivenName") or "").strip(),
             str(visitor.get("visitorFamilyName") or "").strip())
    if names != (str(person.get("personGivenName") or "").strip(),
                 str(person.get("personFamilyName") or "").strip()):
        raise HikError("As consultas do HikCentral retornaram nomes diferentes para este perfil.")
    cards = person.get("cards") or []
    if not isinstance(cards, list) or any(not isinstance(card, dict) for card in cards):
        raise HikError("O HikCentral retornou pulseiras em formato inesperado.")
    remark_present = "remark" in person
    remark = str(person.get("remark") or "").strip()
    return (details, names,
            tuple(str(card.get("cardNo") or "").strip() for card in cards),
            remark, remark_present)


def same_photo(left, right):
    """Compare decoded images, allowing the server to recompress a JPEG."""
    with Image.open(BytesIO(left)) as original, Image.open(BytesIO(right)) as observed:
        size = (48, 48)
        a = original.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        b = observed.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        return max(ImageStat.Stat(ImageChops.difference(a, b)).mean) <= 8


def update_profile(client, visitor_id, first, last, *, card="", remark=None, new_photo=b""):
    visitor_id, first, last, card = (str(value or "").strip()
                                     for value in (visitor_id, first, last, card))
    remark = None if remark is None else validate_visitor_remark(remark)
    errors = {}
    if not re.fullmatch(r"[0-9]{1,64}", visitor_id):
        errors["visit"] = "Selecione um perfil de visitante com ID válido."
    for key, value in (("first", first), ("last", last)):
        if not 2 <= len(value) <= 64 or any(char in value for char in "'/\\:*?\"<>|"):
            errors[key] = "Informe de 2 a 64 caracteres válidos."
    if card and not re.fullmatch(r"[A-Za-z0-9]{1,32}", card):
        errors["card"] = "Informe a pulseira com 1 a 32 caracteres alfanuméricos."
    if new_photo:
        try:
            new_photo = prepare_face_jpeg(new_photo)
        except (ValueError, OSError):
            errors["photo"] = "Selecione ou capture uma foto válida."
    if errors:
        raise EditorFieldError(errors)
    details, old_names, old_cards, old_remark, remark_present = read_profile(client, visitor_id)
    changed = []
    update = {}
    if (first, last) != old_names:
        update.update(personGivenName=first, personFamilyName=last)
    if remark is not None and (not remark_present or remark != old_remark):
        update["remark"] = remark
    if card and card not in old_cards:
        if len(old_cards) > 1:
            raise EditorFieldError({"card": "O perfil tem mais de uma pulseira. Selecione a substituição no HikCentral."})
        update["cards"] = [{"cardNo": card}]
    if update:
        error = ""
        try:
            client.post(PERSON_UPDATE, {"personId": visitor_id, **update})
        except HikError as exc:
            error = str(exc)
        details, names, cards, saved_remark, saved_remark_present = read_profile(client, visitor_id)
        mismatches = {}
        if names != (first, last):
            mismatches.update(first="O HikCentral não confirmou o nome e o sobrenome.",
                              last="O HikCentral não confirmou o nome e o sobrenome.")
        elif "personGivenName" in update:
            changed.append("nome e sobrenome")
        if cards != ((card,) if "cards" in update else old_cards):
            mismatches["card"] = "O HikCentral não confirmou a pulseira solicitada."
        elif "cards" in update:
            changed.append("pulseira")
        if "remark" in update:
            if not saved_remark_present or saved_remark != remark:
                mismatches["remark"] = (
                    "O HikCentral não confirmou o Remark do perfil em Person Information. " + error)
            else:
                changed.append("Remark")
        if mismatches:
            failure = EditorFieldError(mismatches)
            failure.profile_saved = list(changed)
            raise failure
    if new_photo:
        try:
            previous = fetch_visitor_photo(client, visitor_id, details=details)
        except HikError:
            previous = b""
        if not previous or not same_photo(new_photo, previous):
            error = ""
            try:
                client.post(FACE_UPDATE, {
                    "personId": visitor_id, "faceData": base64.b64encode(new_photo).decode("ascii"),
                })
            except HikError as exc:
                error = str(exc)
            try:
                details, _, _, _, _ = read_profile(client, visitor_id)
                confirmed = same_photo(new_photo, fetch_visitor_photo(client, visitor_id, details=details))
            except (HikError, OSError, ValueError):
                confirmed = False
            if not confirmed:
                prefix = "Dados já conferidos: " + ", ".join(changed) + ". " if changed else ""
                raise EditorFieldError({"photo": prefix + "A foto não foi confirmada na consulta ao perfil. " + error})
            changed.append("foto")
    return changed
