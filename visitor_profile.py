"""Edit the existing Visitor Information profile through OpenAPI."""
import base64
from datetime import datetime
from io import BytesIO
import re

from PIL import Image, ImageChops, ImageStat

from core import Visitor, prepare_face_jpeg, registration_payload
from edit_request import preserve_visitor_metadata
from edit_errors import EditorFieldError
from hikcentral import HikError, normalize
from remote_history import fetch_visitor_photo, resolve_remote_edit_record, visitor_details


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
    remark_present = "remark" in visitor
    remark = str(visitor.get("remark") or "").strip()
    return (details, names,
            tuple(str(card.get("cardNo") or "").strip() for card in cards),
            remark, remark_present)


def _person_info(client, visitor_id):
    response = client.post(PERSON_INFO, {"personId": str(visitor_id)})
    person = response.get("data") if isinstance(response, dict) else None
    if not isinstance(person, dict) or str(person.get("personId") or "") != str(visitor_id):
        raise HikError("A consulta de pessoa não confirmou o Visitor ID solicitado.")
    return person


def read_visitor_group(client, visitor_id):
    """Map the profile orgIndexCode to the exact Visitor group name."""
    person = _person_info(client, visitor_id)
    group_id = str(person.get("orgIndexCode") or "").strip()
    if not group_id:
        return ""
    matches = [item for item in client.visitor_group_catalog()
               if str(item.get("id") or "").strip() == group_id and item.get("name")]
    return str(matches[0]["name"]).strip() if len(matches) == 1 else ""


def set_visitor_group(client, visitor_id, group):
    """Write and verify a Visitor group using PersonInfo.orgIndexCode."""
    group = str(group or "").strip()
    if not group:
        return False
    catalog = client.visitor_group_catalog()
    matches = [item for item in catalog
               if normalize(str(item.get("name") or "")) == normalize(group)]
    if len(matches) != 1 or not str(matches[0].get("id") or "").strip():
        raise EditorFieldError({"group": "Selecione um grupo único existente no HikCentral."})
    group_id = str(matches[0]["id"]).strip()
    before = _person_info(client, visitor_id)
    current_id = str(before.get("orgIndexCode") or "").strip()
    if current_id == group_id:
        return False
    write_error = None
    try:
        client.post(PERSON_UPDATE, {"personId": str(visitor_id), "orgIndexCode": group_id})
    except HikError as exc:
        write_error = exc
    observed = _person_info(client, visitor_id)
    if str(observed.get("orgIndexCode") or "").strip() != group_id:
        raise EditorFieldError({"group": "O HikCentral não confirmou o grupo selecionado no Visitor Information."}) from write_error
    return True



def confirm_visitor_group_after_edit(client, visitor_id, group):
    """Last-write-wins correction: Person and Visitor must retain the selected group.

    HikCentral can silently reset orgIndexCode to Visitors on person, NFC,
    appointment and check-in updates. Restore only the explicitly validated
    group, then read BOTH remote representations. A missing Visitor group is
    not inferred; an explicitly contradictory one blocks false success.
    """
    expected = str(group or "").strip()
    if not expected or normalize(expected) == normalize("Visitors"):
        raise EditorFieldError({"group": "Selecione um grupo Visitor autorizado antes de concluir a edição."})
    set_visitor_group(client, str(visitor_id), expected)
    actual = read_visitor_group(client, str(visitor_id))
    if normalize(actual) != normalize(expected):
        raise EditorFieldError({"group": (
            f"O HikCentral não manteve o grupo {expected} na ficha Person "
            f"(atual: {actual or 'não informado'}). Cadastro ainda pendente.")})
    details = visitor_details(client, str(visitor_id))
    info = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if not isinstance(info, dict) or str(info.get("visitorId") or "") != str(visitor_id):
        raise EditorFieldError({"group": "Visitor Information não confirmou a identidade do mesmo visitante."})
    visitor_group = str(info.get("visitorGroupName") or info.get("visitorGroup") or "").strip()
    if visitor_group and normalize(visitor_group) != normalize(expected):
        raise EditorFieldError({"group": (
            f"A ficha Person mostra {expected}, mas Visitor Information retornou "
            f"{visitor_group}. O grupo NÃO foi confirmado; revise no HikCentral.")})
    return expected


def confirm_checkin_card(client, visitor_id, start, end, record_id, card):
    row = client.find_registration_record(visitor_id, start, end, record_id=record_id)
    if not row or str((row.get("visitorCardInfo") or {}).get("cardNumber") or "").strip() != card:
        raise EditorFieldError({"card": "O HikCentral ainda não confirmou a pulseira no check-in. Confira este cadastro pelo histórico."})


def save_reserved_profile_card(client, visitor_id, first, last, card):
    """A reserva pode descartar cards; confirme a pulseira na ficha da pessoa."""
    _, names, cards, _, _ = read_profile(client, visitor_id)
    if names != (first.strip(), last.strip()):
        raise EditorFieldError({"card": "Confira a identidade e a pulseira do cadastro já criado antes de continuar."})
    if cards != (card,):
        update_profile(client, visitor_id, first, last, card=card)


def update_active_visitor_fields(client, record, *, card=None, remark=None, photo=b""):
    """Grave credencial/Remark no check-in ativo, preservando período e nível."""
    visitor_id = str(record.get("visitor_id") or "")
    checkin_id = str(record.get("registration_record_id") or "")
    start, end = record.get("entry_at"), record.get("exit_at")
    if not (visitor_id and checkin_id and start and end):
        raise EditorFieldError({"visit": "Atualize o histórico e selecione o check-in ativo para editar."})
    row = client.find_registration_record(visitor_id, start, end, active_only=True, record_id=checkin_id)
    if not row:
        raise EditorFieldError({"visit": "O check-in já mudou ou foi encerrado. Atualize o histórico antes de editar."})
    current_card = str((row.get("visitorCardInfo") or {}).get("cardNumber") or "").strip()
    details, names, _, current_remark, _ = read_profile(client, visitor_id)
    wanted_card = card or current_card
    wanted_remark = current_remark if remark is None else validate_visitor_remark(remark)
    changes = []
    if wanted_card != current_card:
        changes.append("pulseira")
    if wanted_remark != current_remark:
        changes.append("Remark")
    if not changes:
        return changes
    info = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if info.get("faces") and not photo:
        raise EditorFieldError({"photo": "O HikCentral não devolveu a foto atual. Recarregue o perfil para preservar a foto na edição do check-in."})
    group = str((row.get("visitorBaseInfo") or {}).get("visitorGroup") or "").strip()
    verified, _ = resolve_remote_edit_record(client, {**record, "group": group})
    if (verified.get("_requires_access_confirmation") or
            len(verified.get("access_level_ids", [])) != 1 or len(verified.get("access_names", [])) != 1):
        raise EditorFieldError({"visit": "O nível atual do check-in não foi confirmado. Atualize o histórico."})
    levels = [{"id": verified["access_level_ids"][0], "name": verified["access_names"][0]}]
    visitor = Visitor(*names, group, photo, wanted_card, datetime.fromisoformat(start),
                      datetime.fromisoformat(end), wanted_remark)
    payload = preserve_visitor_metadata(registration_payload(visitor, editing=True), details)
    failure = None
    returned_id = ""
    try:
        returned_id = client.update_registered_visitor(checkin_id, visitor_id, payload, levels)
    except HikError as exc:
        failure = exc
    observed = client.find_registration_record(visitor_id, start, end, active_only=True,
                                               record_id=returned_id)
    mismatches = {}
    if not observed:
        raise EditorFieldError({"visit": "A edição foi enviada, mas o check-in não foi confirmado. Atualize o histórico."}) from failure
    if normalize(str((observed.get("visitorBaseInfo") or {}).get("visitorGroup") or "")) != normalize(group):
        mismatches["group"] = "O HikCentral não confirmou o grupo original neste check-in."
    if wanted_card and str((observed.get("visitorCardInfo") or {}).get("cardNumber") or "").strip() != wanted_card:
        mismatches["card"] = "O HikCentral não confirmou a pulseira neste check-in."
    if read_visitor_remark(client, visitor_id) != wanted_remark:
        mismatches["remark"] = "O HikCentral não confirmou o Remark em Visitor Information."
    if mismatches:
        error = EditorFieldError(mismatches)
        labels = {"card": "pulseira", "remark": "Remark"}
        error.profile_saved = [label for field, label in labels.items()
                               if label in changes and field not in mismatches]
        raise error from failure
    return changes


def same_photo(left, right):
    """Compare decoded images, allowing the server to recompress a JPEG."""
    with Image.open(BytesIO(left)) as original, Image.open(BytesIO(right)) as observed:
        size = (48, 48)
        a = original.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        b = observed.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        return max(ImageStat.Stat(ImageChops.difference(a, b)).mean) <= 8


def update_profile(client, visitor_id, first, last, *, card="", new_photo=b"", group=""):
    visitor_id, first, last, card = (str(value or "").strip()
                                     for value in (visitor_id, first, last, card))
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
    details, old_names, old_cards, _, _ = read_profile(client, visitor_id)
    person = _person_info(client, visitor_id)
    profile = details.get("VisitorInfo") or details.get("visitorInfo") or details
    document_no = str(person.get("certificateNo") or
                      (profile.get("certificateNo") if isinstance(profile, dict) else "") or "").strip()
    document_was_card = bool(card and document_no and
                             document_no.casefold() == card.casefold())
    changed = []
    update = {}
    if (first, last) != old_names:
        update.update(personGivenName=first, personFamilyName=last)
    if card and (old_cards != (card,) or document_was_card):
        update["cards"] = [{"cardNo": card}]
    if document_was_card:
        update["certificateNo"] = ""
    if update:
        write_error = None
        try:
            client.post(PERSON_UPDATE, {"personId": visitor_id, **update})
        except HikError as exc:
            write_error = exc
        details, names, cards, _, _ = read_profile(client, visitor_id)
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
        if document_was_card:
            confirmed_person = _person_info(client, visitor_id)
            confirmed_profile = details.get("VisitorInfo") or details.get("visitorInfo") or details
            observed_document = str(confirmed_person.get("certificateNo") or
                                    (confirmed_profile.get("certificateNo")
                                     if isinstance(confirmed_profile, dict) else "") or "").strip()
            if observed_document and observed_document.casefold() == card.casefold():
                mismatches["card"] = (
                    "O HikCentral ainda retorna a pulseira no campo de identificação do documento.")
        if mismatches:
            failure = EditorFieldError(mismatches)
            failure.profile_saved = list(changed)
            raise failure from write_error
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
    # Persist group only AFTER all other profile/face writes; HikCentral's
    # partial Person updates can silently fall back to its default Visitors.
    if group:
        try:
            if set_visitor_group(client, visitor_id, group):
                changed.append("grupo")
        except Exception as exc:
            exc.profile_saved = [*changed, *getattr(exc, "profile_saved", [])]
            raise
    return changed
