"""Reschedule a reservation still awaiting check-in through Visitor OpenAPI."""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import re

from core import VALE_VISITOR_LEVELS, TZ, _write_json_atomic
from edit_errors import EditorFieldError
from edit_journal import select_edit_journal
from hikcentral import HikError, normalize
from ordered_reservation_change import _lock_transaction, _write_once
from remote_history import compare_reservation_period, parse_access_levels, visitor_details
from reservation_replacement import _booking_by_code
from visitor_profile import validate_visitor_remark


UPDATE_BOOKING = "/artemis/api/visitor/v2/appointment/update"


def _period(start, end):
    dates = []
    errors = {}
    for field, value in (("entry", start), ("exit", end)):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is None:
                raise ValueError("Fuso horário ausente")
            dates.append(parsed.astimezone(TZ).isoformat(timespec="seconds"))
        except (TypeError, ValueError):
            errors[field] = "Informe uma data válida com o horário configurado."
    if errors:
        raise EditorFieldError(errors)
    if datetime.fromisoformat(dates[0]) >= datetime.fromisoformat(dates[1]):
        raise EditorFieldError({"exit": "A saída deve ser posterior à entrada."})
    return tuple(dates)


def _confirm_reserved(client, row, vid, level, group, *, allow_registered=False,
                      expected_status=None):
    info = row.get("visitorInfo") or {}
    if str(info.get("visitorId") or "") != vid:
        raise EditorFieldError({"appointment": "A reserva pertence a outro visitante."})
    if not allow_registered and str(row.get("appointStatus")) != "0":
        raise EditorFieldError({"appointment": "A reserva não está aguardando check-in. Abra a visita no histórico."})
    if expected_status is not None and str(row.get("appointStatus")) != str(expected_status):
        raise EditorFieldError({"appointment": "O estado da reserva mudou no HikCentral; confira antes de continuar."})
    if parse_access_levels(row) != [level]:
        raise EditorFieldError({"group": "O nível de acesso da reserva difere do nível Visitor selecionado."})
    returned_group = str(info.get("visitorGroupName") or info.get("visitorGroup") or "").strip()
    if group is not None and returned_group and normalize(returned_group) != normalize(group):
        raise EditorFieldError({"group": "O HikCentral retornou outro grupo para a reserva."})
    if not allow_registered and client.find_registration_record(vid, row["appointStartTime"], row["appointEndTime"]):
        raise EditorFieldError({"appointment": "O HikCentral já tem um check-in nesse período. Abra a visita no histórico."})


def _request(client, original, vid, start, end, remark, group, level, *, expected_profile_group=None):
    details = visitor_details(client, vid)
    profile = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if str(profile.get("visitorId") or "") != vid:
        raise EditorFieldError({"appointment": "O HikCentral não confirmou o mesmo perfil da reserva."})
    profile_group = str(profile.get("visitorGroupName") or profile.get("visitorGroup") or "").strip()
    expected_profile_group = group if expected_profile_group is None else expected_profile_group
    if expected_profile_group and profile_group and normalize(profile_group) != normalize(expected_profile_group):
        raise EditorFieldError({"group": "O grupo do perfil difere do grupo da reserva."})
    saved = original["visitorInfo"]
    source = {**profile, **{key: value for key, value in saved.items() if value is not None}}
    actual_group = group if group else (
        str(saved.get("visitorGroupName") or saved.get("visitorGroup") or "").strip() or profile_group)
    if not all(str(source.get(key) or "").strip() for key in ("visitorGivenName", "visitorFamilyName")):
        raise EditorFieldError({"appointment": "O HikCentral não retornou nome e sobrenome do perfil."})
    # Reuse the reservation's data and preserve its access settings. The edit
    # endpoint takes profile fields but card and face writes belong to a separate flow.
    info = {key: deepcopy(source[key]) for key in (
        "visitorGivenName", "visitorFamilyName", "gender", "email", "phoneNo", "plateNo",
        "companyName", "certificateType", "certificateNo", "customField", "additionalInfo",
        "watchListInfo",
    ) if key in source and source[key] is not None}
    if "customField" not in info and source.get("customFieldList"):
        info["customField"] = deepcopy(source["customFieldList"])
    info.setdefault("gender", 0)
    prior_access = {**(profile.get("accessInfo") or {}), **(saved.get("accessInfo") or {})}
    # The booking API makes group optional. If the server omitted it, preserve
    # the exact access level without submitting an inferred group assignment.
    if expected_profile_group is not None:
        # A deliberate group migration must send the new Visitor group.
        info["visitorGroupName"] = group
    else:
        # Preserve a group explicitly returned by the profile/reservation.
        # Do not infer and write one from an access-level name when the API
        # omitted the Visitor group; the original level is verified separately.
        confirmed_group = str(saved.get("visitorGroupName") or saved.get("visitorGroup") or
                              profile_group).strip()
        if confirmed_group:
            info["visitorGroupName"] = confirmed_group
    info.update(visitorId=vid, remark=remark, accessInfo={
        "electrostaticDetectionType": prior_access.get("electrostaticDetectionType", 0),
        "qrCodeValidNum": prior_access.get("qrCodeValidNum", 0),
    })
    receptionist = original.get("receptionistInfo") or {}
    receptionist_id = original.get("receptionistId")
    if not receptionist_id and isinstance(receptionist, dict):
        receptionist_id = receptionist.get("receptionistId")
    reason_type = original.get("visitReasonType")
    if reason_type is None:
        reason_type = 4
    reason_detail = original.get("visitReasonDetail") or ""
    if not reason_detail and str(reason_type) == "4":
        reason_detail = "Ingresso diario"
    return {
        "appointRecordId": compare_reservation_period(original, start, end)["appointment_id"],
        "receptionistId": str(receptionist_id or ""),
        "appointStartTime": start,
        "appointEndTime": end,
        "visitReasonType": reason_type,
        "visitReasonDetail": reason_detail,
        "accessInfo": client.access_info([level]),
        "visitorInfoList": [{"VisitorInfo": info}],
    }


def _confirmed(client, row, vid, start, end, remark, level, group, *,
               allow_registered=False, expected_status=None):
    if not row:
        return None
    period = compare_reservation_period(row, start, end)
    if period["state"] != "matched" or str(row["visitorInfo"].get("remark") or "").strip() != remark:
        return None
    _confirm_reserved(client, row, vid, level, group, allow_registered=allow_registered,
                      expected_status=expected_status)
    if not re.fullmatch(r"[0-9]{1,64}", period["appointment_id"]):
        raise HikError("O HikCentral não confirmou o ID da reserva atualizada.")
    return {"state": "confirmed", "operation": "reservation_update", "reservation_period": period,
            "confirmed_appointment_id": period["appointment_id"], "confirmed_visitor_id": vid}


def reschedule_unregistered_reservation(client, record, start, end, remark, selected, *,
                                        journal=None, allow_group_change=False):
    """Edit reservation dates/Remark, or its group while keeping dates unchanged."""
    vid = str(record.get("visitor_id") or "").strip()
    aid = str(record.get("appointment_id") or "").strip()
    code = str(record.get("reservation_code") or (record.get("_reservation_period") or {}).get("code") or "").strip()
    if not (re.fullmatch(r"[0-9]{1,48}", vid) and re.fullmatch(r"[0-9]{1,64}", aid)
            and re.fullmatch(r"[0-9]{1,8}", code)):
        raise EditorFieldError({"appointment": "Selecione uma reserva confirmada com código e Visitor ID."})
    if (not allow_group_change and any(record.get(field) for field in
            ("registration_record_id", "_selected_checkin_id", "_selected_checkin_record"))):
        raise EditorFieldError({"appointment": "Esta visita já tem check-in. Abra a visita no histórico."})
    if len(selected) != 1 or not selected[0].get("id"):
        raise EditorFieldError({"group": "Confirme o nível Visitor original da reserva."})
    level = {"id": str(selected[0]["id"]), "name": selected[0].get("name")}
    group = next((name for name, access in VALE_VISITOR_LEVELS.items() if access == level["name"]), "")
    record_group = str(record.get("_original_group") or record.get("group") or "").strip()
    group_changed = bool(allow_group_change and group and
                         (not record_group or normalize(group) != normalize(record_group)))
    if group_changed and not allow_group_change:
        raise EditorFieldError({"group": "O grupo e o nível Visitor da reserva divergem."})
    if not group and not group_changed:
        # Algumas reservas legítimas retornam somente um nível type=2
        # (por exemplo, ENTRADA ACQUAVALE), sem nome de grupo Visitor.
        # Permitir a edição apenas quando ID e nome enviados pelo histórico
        # confirmam que o nível escolhido é exatamente o da reserva original.
        original_names = record.get("access_names") or []
        original_ids = record.get("access_level_ids") or []
        exact_original = (
            len(original_names) == 1 and len(original_ids) == 1 and
            normalize(str(original_names[0])) == normalize(str(level["name"])) and
            str(original_ids[0]) == str(level["id"]))
        if not exact_original:
            raise EditorFieldError({
                "group": "O nível não tem grupo Visitor mapeado e não foi possível "
                        "confirmar o mesmo ID e nome da reserva original."
            })
        group = record_group
    start, end = _period(start, end)
    old_start, old_end = _period(
        record.get("reservation_entry_at") or record.get("entry_at"),
        record.get("reservation_exit_at") or record.get("exit_at"))
    remark = validate_visitor_remark(remark)
    scope = {
        "server": str(getattr(client, "url", "")), "visitor_id": vid, "code": code,
        "start": start, "end": end, "level_id": level["id"],
        "group": group, "original_group": record_group,
        "remark_digest": sha256(remark.encode("utf-8")).hexdigest(),
    }
    journal = journal or select_edit_journal(f"agenda:{vid}:{code}")

    with _lock_transaction(journal):
        if journal.exists():
            try:
                pending = json.loads(journal.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise HikError("O registro local da edição da reserva não pôde ser lido.") from exc
            if pending != scope:
                raise HikError("Há uma edição pendente para esta reserva. Confira as datas e o apartamento enviados.")
            observed = _booking_by_code(client, code, start, end, vid)
            if allow_group_change and observed:
                observed_group = str((observed.get("visitorInfo") or {}).get("visitorGroupName") or
                                     (observed.get("visitorInfo") or {}).get("visitorGroup") or "").strip()
                if not observed_group or normalize(observed_group) != normalize(group):
                    raise HikError("A troca de grupo anterior não foi confirmada. Confira a reserva no HikCentral antes de repetir.")
            result = _confirmed(client, observed, vid, start, end, remark, level, group,
                                allow_registered=allow_group_change)
            if not result:
                raise HikError("O HikCentral ainda não confirmou a edição anterior. Confira a reserva pelo código.")
            journal.unlink()
            return result

        original = _booking_by_code(client, code, old_start, old_end, vid)
        if not original:
            raise EditorFieldError({"appointment": "A reserva mudou no HikCentral. Busque pelo código novamente."})
        old_period = compare_reservation_period(original, old_start, old_end)
        if old_period["state"] != "matched" or old_period["appointment_id"] != aid:
            raise EditorFieldError({"appointment": "A reserva mudou no HikCentral. Busque pelo código novamente."})
        original_info = original.get("visitorInfo") or {}
        original_group = str(original_info.get("visitorGroupName") or
                             original_info.get("visitorGroup") or "").strip()
        if record_group and original_group and normalize(record_group) != normalize(original_group):
            raise EditorFieldError({"group": "O grupo original mudou no HikCentral; confira antes de salvar."})
        original_levels = parse_access_levels(original)
        if not original_levels:
            raise EditorFieldError({"group": "O HikCentral não retornou o nível de acesso original da reserva."})
        old_names, old_ids = record.get("access_names") or [], record.get("access_level_ids") or []
        if old_names and (len(old_names) != len(original_levels) or
                          any(normalize(str(name)) != normalize(str(level_row["name"]))
                              for name, level_row in zip(old_names, original_levels))):
            raise EditorFieldError({"group": "O nível da reserva difere do histórico carregado."})
        if old_ids and (len(old_ids) != len(original_levels) or
                        any(str(value) != str(level_row["id"])
                            for value, level_row in zip(old_ids, original_levels))):
            raise EditorFieldError({"group": "O ID do nível da reserva difere do histórico carregado."})
        original_status = str(original.get("appointStatus") or "")
        result = None if group_changed else _confirmed(
            client, original, vid, start, end, remark, level, group,
            allow_registered=allow_group_change,
            expected_status=original_status if allow_group_change else None)
        if result:
            return result
        if group_changed:
            if not original_group:
                raise EditorFieldError({"group": "O grupo original da reserva não foi retornado pelo HikCentral."})
            _confirm_reserved(client, original, vid, original_levels[0], original_group or None,
                              allow_registered=True, expected_status=original_status)
        else:
            _confirm_reserved(client, original, vid, level, group)
        if (start, end) != (old_start, old_end):
            if datetime.fromisoformat(end) <= datetime.now(TZ):
                raise EditorFieldError({"exit": "Informe uma saída que ainda não expirou."})
            if (not allow_group_change and
                    client.find_registration_record(vid, start, end, active_only=True)):
                raise EditorFieldError({"entry": "Já existe um check-in para o visitante no período solicitado."})
        body = _request(client, original, vid, start, end, remark, group, level,
                        expected_profile_group=(record_group or "") if group_changed else None)
        _write_json_atomic(journal, scope)
        _, write_error = _write_once(lambda: client.post(UPDATE_BOOKING, body))
        result = _confirmed(client, _booking_by_code(client, code, start, end, vid),
                            vid, start, end, remark, level, group,
                            allow_registered=allow_group_change,
                            expected_status=original_status if allow_group_change else None)
        if not result:
            raise HikError("O HikCentral não confirmou as novas datas, o Remark e o grupo da reserva. "
                           "Confira a reserva pelo código antes de continuar.") from write_error
        journal.unlink()
        return result
