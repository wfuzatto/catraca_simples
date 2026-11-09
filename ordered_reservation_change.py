"""Edit a linked reservation through HikCentral OpenAPI, preserving Visitor ID."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
import re

from core import _write_json_atomic, VALE_VISITOR_LEVELS, TZ, verify_visitor_access_plan
from edit_errors import EditorFieldError
from edit_journal import select_edit_journal
from edit_request import validate_edit_request
from hikcentral import HikError, normalize
from remote_history import (
    APPOINTMENTS_PATH, compare_reservation_period, fetch_visitor_photo,
    find_original_appointment, original_appointment_identifier, parse_access_levels, visitor_details,
)
from reservation_replacement import _booking_by_code
from visitor_edit_transaction import _confirmed_edit
from visitor_history import save_registration
from visitor_profile import same_photo


CHECKOUT = "/artemis/api/visitor/v1/visitor/out"
UPDATE_BOOKING = "/artemis/api/visitor/v2/appointment/update"
CLOSED_STATES = {"1", "2", "3", "4"}
STAGES = {"prepared", "checkout_inflight", "checked_out", "profile_inflight", "profile_updated",
          "update_inflight", "updated",
          "checkin_inflight", "checked_in", "apply_inflight", "applied"}
LEGACY_DESTRUCTIVE_STAGES = {"delete_inflight", "deleted", "create_inflight", "created",
                             "identity_conflict"}


@contextmanager
def _lock_transaction(path):
    """Release on process exit, without removing a lock file held by another instance."""
    lock_path = path.with_suffix(".lock")
    if lock_path.is_symlink():
        raise HikError("O registro local da alteração contém um vínculo inválido.")
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            acquire = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            raise HikError("Já existe uma alteração em andamento para este visitante.") from exc
        try:
            yield
        finally:
            lock.seek(0)
            release()


def _id(value):
    return str(value or "").strip()


def _write_once(action):
    """A failed response is reconciled by a read before any retry."""
    try:
        return action(), None
    except Exception as exc:
        return None, exc


def _booking_at_period(client, visitor_id, start, end):
    """Return the one exact visitor reservation at a period, or fail closed."""
    query = {
        "pageSize": 100, "appointState": -1, "sortField": "VisitingTime", "orderType": 1,
        "appointStartTime": (datetime.fromisoformat(start) - timedelta(days=1)).isoformat(),
        "appointEndTime": (datetime.fromisoformat(end) + timedelta(days=1)).isoformat(),
    }
    matches = {}
    for page in range(1, 11):
        data = client.post(APPOINTMENTS_PATH, {**query, "pageNo": page}).get("data") or {}
        if not isinstance(data, dict):
            raise HikError("O HikCentral retornou uma resposta inválida ao consultar reservas.")
        rows = data.get("list")
        if rows is None and str(data.get("total")) == "0":
            rows = []
        if not isinstance(rows, list):
            raise HikError("O HikCentral não retornou uma lista válida de reservas.")
        for row in rows:
            if _id((row.get("visitorInfo") or {}).get("visitorId")) != visitor_id:
                continue
            try:
                if compare_reservation_period(row, start, end)["state"] != "matched":
                    continue
                aid = original_appointment_identifier(row)
            except (TypeError, ValueError, KeyError):
                continue
            if not aid:
                raise HikError("A reserva encontrada não tem um ID válido.")
            if aid in matches and matches[aid] != row:
                raise HikError("A consulta retornou dados divergentes para a mesma reserva.")
            matches[aid] = row
        total = int(data.get("total") or 0)
        if len(rows) < 100 or (total and page * 100 >= total):
            break
    else:
        raise HikError("Há reservas demais para confirmar as datas com segurança.")
    if len(matches) > 1:
        raise HikError("Há mais de uma reserva deste visitante nas datas solicitadas.")
    return next(iter(matches.values()), None)


def _booking_by_code_at_period(client, code, visitor_id, start, end):
    """Use the stable reservation code to read a reservation across its states.

    This HikCentral installation omits checked-out/registered reservations from
    the unfiltered appointment list even though a lookup by reservation code
    returns them. The code is confirmed against the visitor ID and exact period.
    """
    code = _id(code)
    if not code:
        return None
    row = _booking_by_code(client, code, start, end, visitor_id)
    if not row:
        return None
    try:
        if compare_reservation_period(row, start, end)["state"] == "matched":
            return row
    except (TypeError, ValueError, KeyError) as exc:
        raise HikError("A reserva localizada pelo código retornou datas inválidas.") from exc
    return None


def _confirm_original_period_moved(client, plan):
    old = _booking_by_code(client, plan["old_code"], plan["old_booking_entry"],
                           plan["old_booking_exit"], plan["visitor_id"])
    if old:
        try:
            still_old = compare_reservation_period(
                old, plan["old_booking_entry"], plan["old_booking_exit"])["state"] == "matched"
        except (TypeError, ValueError, KeyError) as exc:
            raise HikError("A conferência da reserva anterior retornou datas inválidas.") from exc
        if still_old:
            raise HikError("O HikCentral mostrou as novas datas, mas também manteve a reserva antiga. "
                           "A edição foi interrompida para evitar reservas duplicadas.")
    old = _booking_at_period(client, plan["visitor_id"],
                             plan["old_booking_entry"], plan["old_booking_exit"])
    if old:
        raise HikError("O HikCentral mostrou as novas datas, mas também manteve a reserva antiga. "
                       "A edição foi interrompida para evitar reservas duplicadas.")


def _reservation_matches_request(row, payload, visitor_id):
    saved = row.get("visitorInfo") or {}
    desired = payload["visitorInfoList"][0]["VisitorInfo"]
    if _id(saved.get("visitorId")) != visitor_id:
        return False
    keys = ["visitorGivenName", "visitorFamilyName", "remark"]
    if desired.get("visitorGroupName"):
        keys.append("visitorGroupName")
    return all(str(saved.get(key) or "").strip() == str(desired.get(key) or "").strip()
               for key in keys)


def _reservation_update_body(client, original, payload, visitor_id, level):
    """Build the documented v2 update payload without resending face/card data."""
    try:
        desired = deepcopy(payload["visitorInfoList"][0]["VisitorInfo"])
        start, end = str(payload["visitStartTime"]), str(payload["visitEndTime"])
    except (KeyError, IndexError, TypeError) as exc:
        raise EditorFieldError({"visit": "Os campos obrigatórios da reserva estão incompletos."}) from exc
    if not str(desired.get("visitorGivenName") or "").strip():
        raise EditorFieldError({"first": "Informe o nome antes de atualizar a reserva."})
    if not str(desired.get("visitorFamilyName") or "").strip():
        raise EditorFieldError({"last": "Informe o sobrenome antes de atualizar a reserva."})
    if not str(desired.get("remark") or "").strip():
        raise EditorFieldError({"reservation": "Informe o apartamento/Remark antes de atualizar a reserva."})

    saved = original.get("visitorInfo") or {}
    details = visitor_details(client, visitor_id)
    profile = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if _id(profile.get("visitorId")) != visitor_id:
        raise EditorFieldError({"appointment": "O HikCentral não confirmou o mesmo perfil da reserva."})
    allowed = (
        "visitorGivenName", "visitorFamilyName", "gender", "email", "phoneNo", "plateNo",
        "companyName", "certificateType", "certificateNo", "customField", "additionalInfo",
        "watchListInfo", "visitorGroupName",
    )
    info = {}
    for key in allowed:
        value = profile.get(key)
        if key in saved and saved[key] is not None:
            value = saved[key]
        if key in desired and desired[key] is not None:
            value = desired[key]
        if value is not None:
            info[key] = deepcopy(value)
    if "customField" not in info:
        custom_fields = desired.get("customFieldList") or saved.get("customFieldList") or profile.get("customFieldList")
        if custom_fields:
            info["customField"] = deepcopy(custom_fields)
    info["visitorId"] = visitor_id
    info["remark"] = str(desired["remark"]).strip()
    info.setdefault("gender", 0)
    info.pop("cards", None)
    info.pop("faces", None)
    info["accessInfo"] = {
        "electrostaticDetectionType": (saved.get("accessInfo") or {}).get("electrostaticDetectionType", 0),
        "qrCodeValidNum": (saved.get("accessInfo") or {}).get("qrCodeValidNum", 0),
    }
    receptionist = original.get("receptionistInfo") or {}
    receptionist_id = original.get("receptionistId") or (
        receptionist.get("receptionistId") if isinstance(receptionist, dict) else "")
    reason_type = original.get("visitReasonType") or 4
    reason_detail = original.get("visitReasonDetail") or (
        "Ingresso diario" if str(reason_type) == "4" else "")
    return {
        "appointRecordId": original_appointment_identifier(original),
        "receptionistId": str(receptionist_id or ""),
        "appointStartTime": start,
        "appointEndTime": end,
        "visitReasonType": reason_type,
        "visitReasonDetail": reason_detail,
        "accessInfo": client.access_info([level]),
        "visitorInfoList": [{"VisitorInfo": info}],
    }


def change_reservation_dates(client, record, payload, selected, doors, visitor, group, requested_level,
                             *, journal=None, profile_update=None):
    """Check out if needed, update the linked reservation through OpenAPI, then check in."""
    validate_edit_request(payload)
    reservation_info = (payload.get("visitorInfoList") or [{}])[0].get("VisitorInfo") or {}
    if not str(reservation_info.get("remark") or "").strip():
        raise EditorFieldError({
            "reservation": "O Remark obrigatório da reserva está vazio. Informe-o em Visitor Information antes de alterar as datas."})
    vid = _id(record.get("visitor_id"))
    if not re.fullmatch(r"\d{1,48}", vid):
        raise EditorFieldError({"visit": "Selecione um cadastro com Visitor ID válido."})
    verify_visitor_access_plan(selected, doors, requested_level)
    level = {"id": _id(selected[0]["id"]), "name": selected[0]["name"]}
    approved = VALE_VISITOR_LEVELS.get(group)
    info = payload["visitorInfoList"][0]["VisitorInfo"]
    if (not approved or list(requested_level) != [approved] or level["name"] != approved
            or normalize(str(record.get("group") or "")) != normalize(group)
            or normalize(str(info.get("visitorGroupName") or "")) != normalize(group)):
        raise EditorFieldError({"group": "Confirme o grupo e o nível Visitor originais."})
    if "remark" not in info or str(info["remark"] or "").strip() != visitor.reservation.strip():
        raise EditorFieldError({"reservation": "Confira o apartamento/Remark informado."})
    if bool(info.get("cards")) != bool(visitor.card_number) or bool(info.get("faces")) != bool(visitor.photo_jpeg):
        raise EditorFieldError({"credential": "Confirme todas as credenciais que serão reaplicadas."})
    start, end = payload["visitStartTime"], payload["visitEndTime"]
    journal = journal or select_edit_journal("troca:" + vid)
    scope = {"server": str(getattr(client, "url", "")), "visitor_id": vid,
             "start": start, "end": end, "group": group, "level_id": level["id"],
             "doors": sorted(str(door) for door in doors)}

    with _lock_transaction(journal):
        details = visitor_details(client, vid)
        visitor_info = details.get("VisitorInfo") or details.get("visitorInfo") or details
        if _id(visitor_info.get("visitorId")) != vid:
            raise EditorFieldError({"visit": "O HikCentral precisa confirmar o perfil do visitante."})
        complete_payload = deepcopy(payload)
        # Profile metadata has already been written by Visitor Information; retain
        # its additional fields while allowing the operator's edits to take priority.
        saved_info = visitor_info
        desired_info = complete_payload["visitorInfoList"][0]["VisitorInfo"]
        for key, value in saved_info.items():
            if key not in desired_info and key not in {"cards", "faces"}:
                desired_info[key] = deepcopy(value)
        validate_edit_request(complete_payload)
        return _update_linked_reservation(client, record, complete_payload, selected, doors, visitor,
                                          group, requested_level, scope, level, journal,
                                          profile_update=profile_update)


def _update_linked_reservation(client, record, payload, selected, doors, visitor, group,
                               requested_level, scope, level, journal, *, profile_update=None):
    vid, start, end, group = (scope[key] for key in ("visitor_id", "start", "end", "group"))
    old_checkin = _id(record.get("registration_record_id"))
    old_start, old_end = record.get("entry_at"), record.get("exit_at")
    original_id = _id(record.get("appointment_id") or record.get("_original_appointment_id"))
    chosen = record.get("_selected_checkin_record")
    if isinstance(chosen, dict):
        base = chosen.get("visitorBaseInfo") or {}
        if _id(base.get("visitorId")) != vid:
            raise EditorFieldError({"visit": "A visita selecionada pertence a outro visitante."})
        old_checkin = _id(chosen.get("recordId"))
        old_start, old_end = base.get("visitStartTime"), base.get("visitEndTime")

    if journal.exists():
        try:
            plan = json.loads(journal.read_text(encoding="utf-8"))
            if plan.get("stage") in LEGACY_DESTRUCTIVE_STAGES:
                raise HikError("Há uma tentativa antiga de excluir/recriar esta reserva. Confira o vínculo no HikCentral antes de continuar.")
            valid = (all(plan.get(key) == value for key, value in scope.items())
                     and plan.get("stage") in STAGES
                     and plan.get("old_checkin") in {old_checkin, plan.get("new_checkin")}
                     and bool(re.fullmatch(r"\d{1,64}", str(plan.get("old_id") or "")))
                     and bool(re.fullmatch(r"\d{1,8}", str(plan.get("old_code") or ""))))
        except (OSError, ValueError, TypeError):
            valid = False
            plan = {}
        if not valid or (original_id and original_id not in (plan["old_id"], plan.get("new_id", plan["old_id"]))):
            raise HikError("Já existe uma alteração pendente. Reabra o mesmo visitante e informe os mesmos dados.")
    else:
        if datetime.fromisoformat(end) <= datetime.now(TZ):
            raise EditorFieldError({"exit": "Para alterar a reserva, informe uma saída que ainda não expirou."})
        if not old_checkin or not old_start or not old_end:
            raise EditorFieldError({"visit": "Selecione a visita anterior que será encerrada."})
        original = find_original_appointment(client, {
            **record, "entry_at": old_start, "exit_at": old_end, "appointment_id": original_id,
        })
        old_period = compare_reservation_period(original, old_start, old_end)
        if (not re.fullmatch(r"\d{1,64}", old_period["appointment_id"])
                or not re.fullmatch(r"\d{1,8}", old_period["code"])
                or _id((original.get("visitorInfo") or {}).get("visitorId")) != vid):
            raise EditorFieldError({"appointment": "Confirme a reserva original vinculada ao mesmo visitante."})
        if (old_period["entry_at"], old_period["exit_at"]) == (start, end):
            raise EditorFieldError({"entry": "A reserva já está nas datas solicitadas."})
        if parse_access_levels(original) != [level]:
            raise EditorFieldError({"group": "O nível da reserva original difere do nível Visitor selecionado."})
        existing_target = _booking_at_period(client, vid, start, end)
        if existing_target:
            raise EditorFieldError({"entry": "Já existe uma reserva deste visitante nas datas solicitadas."})
        row = _old_visit(client, {"visitor_id": vid, "old_checkin": old_checkin,
                                  "old_entry": old_start, "old_exit": old_end, "group": group})
        active = client.find_registration_record(vid, start, end, active_only=True)
        if active:
            raise EditorFieldError({"visit": "Já existe uma visita ativa do visitante nas datas solicitadas."})
        if ((record.get("expect_card") or (row.get("visitorCardInfo") or {}).get("cardNumber"))
                and not visitor.card_number):
            raise EditorFieldError({"card": "Informe a pulseira antes de alterar as datas."})
        if record.get("expect_face") and not visitor.photo_jpeg:
            raise EditorFieldError({"photo": "Informe a foto antes de alterar as datas."})
        plan = {**scope, "stage": "prepared", "old_checkin": old_checkin,
                "old_id": old_period["appointment_id"], "old_code": old_period["code"],
                "old_entry": old_start, "old_exit": old_end,
                "old_booking_entry": old_period["entry_at"], "old_booking_exit": old_period["exit_at"],
                "update_attempts": 0}
        _write_json_atomic(journal, plan)

    def save(stage, **extra):
        plan.update(stage=stage, **extra)
        _write_json_atomic(journal, plan)

    if plan["stage"] == "prepared":
        _old_booking(client, plan, require=True)
        row = _old_visit(client, plan)
        if str(row["visitorStatus"]) == "0":
            save("checkout_inflight")
            _, failure = _write_once(lambda: client.post(CHECKOUT, {"appointRecordId": plan["old_checkin"]}))
        else:
            save("checked_out")
            failure = None
    else:
        failure = None
    if plan["stage"] == "checkout_inflight":
        if str(_old_visit(client, plan)["visitorStatus"]) not in CLOSED_STATES:
            raise HikError("O HikCentral ainda não confirmou o checkout. Confira a visita antes de repetir.") from failure
        save("checked_out")

    if plan["stage"] in {"checked_out", "profile_inflight"}:
        if profile_update:
            save("profile_inflight")
            profile_update()
        save("profile_updated")

    if plan["stage"] in {"profile_updated", "update_inflight"}:
        if str(_old_visit(client, plan)["visitorStatus"]) not in CLOSED_STATES:
            raise HikError("A visita anterior ainda está ativa; a reserva não foi alterada.")
        target_code = _id(plan.get("new_code") or plan["old_code"])
        updated = _booking_by_code_at_period(client, target_code, vid, start, end)
        if updated:
            if plan["stage"] != "update_inflight":
                raise HikError("Já existe uma reserva nas novas datas. Confira no HikCentral antes de continuar.")
            _confirm_original_period_moved(client, plan)
            observed_id = original_appointment_identifier(updated)
            if not _reservation_matches_request(updated, payload, vid):
                if parse_access_levels(updated) != [level]:
                    raise HikError("A reserva encontrada nas novas datas tem outro nível de acesso.")
                plan["new_id"] = observed_id
                plan["new_code"] = _id(updated.get("appointCode")) or target_code
                body = _reservation_update_body(client, updated, payload, vid, level)
                save("update_inflight", new_id=plan["new_id"], new_code=plan["new_code"],
                     update_attempts=int(plan.get("update_attempts") or 0) + 1)
                update_reply, failure = _write_once(lambda: client.post(UPDATE_BOOKING, body))
                reply_data = (update_reply.get("data") or {}) if isinstance(update_reply, dict) else {}
                response_id = _id(reply_data.get("appointRecordId")) if isinstance(reply_data, dict) else ""
                response_code = _id(reply_data.get("appointCode")) if isinstance(reply_data, dict) else ""
                target_code = response_code or plan["new_code"]
                save("update_inflight", new_id=response_id, new_code=target_code,
                     update_attempts=int(plan.get("update_attempts") or 0) + 1)
                updated = _booking_by_code_at_period(client, target_code, vid, start, end)
                if not updated:
                    raise HikError("A API não confirmou os campos da reserva atualizada; confira antes de repetir.") from failure
                observed_id = original_appointment_identifier(updated)
                if response_id and response_id != observed_id:
                    raise HikError("O ID de reserva devolvido pela API difere da reserva consultada.")
                plan["new_id"] = observed_id
                plan["new_code"] = _id(updated.get("appointCode")) or target_code
                if not _reservation_matches_request(updated, payload, vid):
                    raise HikError("O HikCentral ainda não confirmou o nome, sobrenome e apartamento informados.")
            plan["new_id"] = original_appointment_identifier(updated)
            plan["new_code"] = _id(updated.get("appointCode"))
            if not re.fullmatch(r"\d{1,64}", plan["new_id"]) or not re.fullmatch(r"\d{1,8}", plan["new_code"]):
                raise HikError("O HikCentral não retornou IDs válidos para a reserva atualizada.")
            _confirm_updated_booking(client, updated, start, end, [level])
            save("updated")
        else:
            old = _booking_by_code_at_period(
                client, plan["old_code"], vid,
                plan["old_booking_entry"], plan["old_booking_exit"])
            if not old or original_appointment_identifier(old) != plan["old_id"]:
                raise HikError("A API não confirmou nem a reserva antiga nem as novas datas. Confira no HikCentral.") from failure
            body = _reservation_update_body(client, old, payload, vid, level)
            save("update_inflight", update_attempts=int(plan.get("update_attempts") or 0) + 1)
            update_reply, failure = _write_once(lambda: client.post(UPDATE_BOOKING, body))
            reply_data = (update_reply.get("data") or {}) if isinstance(update_reply, dict) else {}
            response_id = _id(reply_data.get("appointRecordId")) if isinstance(reply_data, dict) else ""
            response_code = _id(reply_data.get("appointCode")) if isinstance(reply_data, dict) else ""
            new_code = response_code or plan["old_code"]
            save("update_inflight", new_id=response_id, new_code=new_code,
                 update_attempts=int(plan.get("update_attempts") or 0) + 1)
            updated = _booking_by_code_at_period(client, new_code, vid, start, end)
            if not updated:
                old_after = _booking_by_code_at_period(
                    client, plan["old_code"], vid,
                    plan["old_booking_entry"], plan["old_booking_exit"])
                if old_after and original_appointment_identifier(old_after) == plan["old_id"]:
                    raise HikError("O HikCentral ainda não confirmou a atualização da reserva pela API. "
                                   "A reserva original foi preservada; tente salvar novamente.") from failure
                raise HikError("A API não confirmou a reserva antiga nem as novas datas. Confira no HikCentral.") from failure
            _confirm_original_period_moved(client, plan)
            observed_id = original_appointment_identifier(updated)
            if response_id and response_id != observed_id:
                raise HikError("O ID de reserva devolvido pela API difere da reserva consultada.")
            plan["new_id"] = observed_id
            plan["new_code"] = _id(updated.get("appointCode")) or new_code
            if not _reservation_matches_request(updated, payload, vid):
                save("update_inflight", new_id=plan["new_id"], new_code=plan["new_code"])
                raise HikError("A reserva foi atualizada, mas a API ainda não confirmou nome, sobrenome e apartamento.") from failure
            if not re.fullmatch(r"\d{1,64}", plan["new_id"]) or not re.fullmatch(r"\d{1,8}", plan["new_code"]):
                raise HikError("O HikCentral não retornou IDs válidos para a reserva atualizada.")
            _confirm_updated_booking(client, updated, start, end, [level])
            save("updated")

    updated = _booking_by_code_at_period(
        client, _id(plan.get("new_code") or plan["old_code"]), vid, start, end)
    if not updated or original_appointment_identifier(updated) != plan.get("new_id"):
        raise HikError("A reserva atualizada não apareceu na conferência OpenAPI.")
    _confirm_original_period_moved(client, plan)
    period = _confirm_updated_booking(client, updated, start, end, [level])
    active = client.find_registration_record(vid, start, end, active_only=True)
    if plan["stage"] == "updated" and active:
        save("checked_in", new_checkin=_id(active.get("recordId")), automatic_checkin=True)
    elif plan["stage"] == "updated":
        save("checkin_inflight", automatic_checkin=False)
        returned, failure = _write_once(lambda: client.register_existing_visitor(
            vid, payload, selected, appointment_id=plan["new_id"]))
        if returned:
            save("checkin_inflight", new_checkin=_id(returned))
        active = client.find_registration_record(vid, start, end, active_only=True)
    rid = _id((active or {}).get("recordId"))
    if not rid or plan.get("new_checkin", rid) != rid:
        raise HikError("A reserva foi atualizada pela API, mas o HikCentral ainda não confirmou o check-in. "
                       "Confira a visita antes de repetir.") from failure
    if plan["stage"] == "checkin_inflight":
        save("checked_in", new_checkin=rid)
    _confirmed_edit(client, vid, payload, visitor, group, rid)
    if visitor.photo_jpeg and not same_photo(visitor.photo_jpeg, fetch_visitor_photo(client, vid)):
        raise EditorFieldError({"photo": "A foto no perfil ainda difere da foto informada."})

    if plan["stage"] in {"checked_in", "apply_inflight"}:
        save("apply_inflight")
        current_levels = parse_access_levels(visitor_details(client, vid))
        if not any(str(item.get("id")) == level["id"] and item.get("name") == level["name"]
                   for item in current_levels):
            client.assign_access(vid, level["id"])
        client.apply(vid, doors)
        save("applied")
    if plan["stage"] != "applied":
        raise HikError("Reserva atualizada pela API; o HikCentral ainda não confirmou a solicitação de acesso.")

    code = _id(updated.get("appointCode"))
    history = {"visitor_id": vid, "appointment_id": plan["new_id"], "registration_record_id": rid,
               "reservation_code": code, "reservation_entry_at": start, "reservation_exit_at": end,
               "entry_at": start, "exit_at": end, "group": group,
               "first": visitor.first_name, "last": visitor.last_name, "reservation": visitor.reservation,
               "door_ids": list(doors), "access_names": list(requested_level),
               "expect_face": bool(visitor.photo_jpeg), "expect_card": bool(visitor.card_number),
               "checkin_state": "check-in efetuado", "state": "submitted"}
    save_registration(history)
    journal.unlink()
    return {"state": "submitted",
            "message": "A reserva foi atualizada pela OpenAPI; o HikCentral controla a distribuição do acesso.",
            "operation": "reservation_update", "rebooking_completed": True, "operation_completed": True,
            "automatic_checkin": bool(plan.get("automatic_checkin")), "confirmed_appointment_id": plan["new_id"],
            "confirmed_checkin_id": rid, "confirmed_group": group, "reservation_period": period,
            "access_receipt_saved": False}


def _old_visit(client, plan):
    row = client.find_registration_record(plan["visitor_id"], plan["old_entry"], plan["old_exit"],
                                          record_id=plan["old_checkin"])
    base = (row or {}).get("visitorBaseInfo") or {}
    if (_id((row or {}).get("recordId")) != plan["old_checkin"]
            or _id(base.get("visitorId")) != plan["visitor_id"]
            or normalize(str(base.get("visitorGroup") or "")) != normalize(plan["group"])
            or str((row or {}).get("visitorStatus")) not in (CLOSED_STATES | {"0"})):
        raise HikError("A visita anterior mudou ou não pode ser confirmada no HikCentral.")
    return row


def _old_booking(client, plan, *, require=False):
    old = _booking_by_code(client, plan["old_code"], plan["old_booking_entry"],
                           plan["old_booking_exit"], plan["visitor_id"])
    if old and (original_appointment_identifier(old) != plan["old_id"]
                or compare_reservation_period(old, plan["old_booking_entry"], plan["old_booking_exit"])["state"]
                != "matched"):
        raise HikError("A reserva anterior mudou durante a alteração. Confira no HikCentral.")
    if require and not old:
        raise HikError("A reserva anterior não foi encontrada para confirmar a alteração.")
    return old


def _confirm_updated_booking(client, booking, start, end, levels):
    """Confirm an edited existing reservation, including HikCentral's state 4."""
    period = compare_reservation_period(booking, start, end)
    if period["state"] != "matched" or parse_access_levels(booking) != levels:
        raise EditorFieldError({"replacement_code": "Confira as datas e o nível original na reserva atualizada."})
    flow = booking.get("approvalFlowCode")
    if flow:
        approval = client.post(
            "/artemis/api/visitor/v1/visitorApprovalFlow/details",
            {"approvalFlowCode": str(flow)}).get("data") or {}
        if str(approval.get("status")) != "1":
            raise EditorFieldError({"replacement_code": "Aguarde a aprovação da reserva atualizada no HikCentral."})
    return period
