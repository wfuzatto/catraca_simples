"""Replace the exact original booking in the operator's requested order."""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
import re

from core import _write_json_atomic, VALE_VISITOR_LEVELS, TZ, verify_visitor_access_plan
from edit_errors import EditorFieldError
from edit_journal import select_edit_journal
from edit_request import preserve_visitor_metadata, validate_edit_request
from hikcentral import HikError, normalize
from remote_history import (
    APPOINTMENTS_PATH, compare_reservation_period, fetch_visitor_photo,
    find_original_appointment, original_appointment_identifier, parse_access_levels, visitor_details,
)
from reservation_replacement import DELETE_BOOKING, _booking_by_code, _confirm_new_booking
from visitor_edit_transaction import _confirmed_edit
from visitor_history import save_registration
from visitor_profile import same_photo


CHECKOUT = "/artemis/api/visitor/v1/visitor/out"
CLOSED_STATES = {"1", "2", "3", "4"}
STAGES = {"prepared", "checkout_inflight", "checked_out", "delete_inflight", "deleted",
          "create_inflight", "created", "checkin_inflight", "checked_in", "apply_inflight",
          "applied", "identity_conflict"}


@contextmanager
def _lock_transaction(path):
    """Release on process exit, without removing a lock file held by another instance."""
    lock_path = path.with_suffix(".lock")
    if lock_path.is_symlink():
        raise HikError("O registro local da troca contém um vínculo inválido.")
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
            raise HikError("Já existe uma troca em andamento para este visitante.") from exc
        try:
            yield
        finally:
            lock.seek(0)
            release()


def _id(value):
    return str(value or "").strip()


def _remote_period_matches(start, end, target_start, target_end):
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        return (a.tzinfo is not None and b.tzinfo is not None
                and a == datetime.fromisoformat(target_start)
                and b == datetime.fromisoformat(target_end))
    except (TypeError, ValueError):
        return False


def _target_booking(client, vid, start, end, old_id):
    """Find a unique booking by identity and period when a create response was lost."""
    query = {
        "pageSize": 100, "appointState": -1, "sortField": "VisitingTime", "orderType": 1,
        "appointStartTime": (datetime.fromisoformat(start) - timedelta(days=1)).isoformat(),
        "appointEndTime": (datetime.fromisoformat(end) + timedelta(days=1)).isoformat(),
    }
    matches = {}
    for page in range(1, 11):
        data = client.post(APPOINTMENTS_PATH, {**query, "pageNo": page}).get("data") or {}
        rows = data.get("list")
        if rows is None and str(data.get("total")) == "0":
            rows = []
        if not isinstance(rows, list):
            raise HikError("O HikCentral não retornou uma lista válida de reservas.")
        for row in rows:
            if (_id((row.get("visitorInfo") or {}).get("visitorId")) != vid
                    or not _remote_period_matches(row.get("appointStartTime"), row.get("appointEndTime"), start, end)):
                continue
            aid = original_appointment_identifier(row)
            if not aid or (aid in matches and matches[aid] != row):
                raise HikError("As consultas retornaram vínculos diferentes para a reserva nova.")
            if aid != old_id:
                matches[aid] = row
        if len(rows) < 100 or int(data.get("total") or 0) in range(1, page * 100 + 1):
            break
    else:
        raise HikError("Há reservas demais para confirmar a troca com segurança.")
    if len(matches) > 1:
        raise HikError("O HikCentral encontrou mais de uma reserva no período solicitado.")
    return next(iter(matches.values()), None)


def _write_once(action):
    """A timeout leaves the remote outcome uncertain. The next action must be a read."""
    try:
        return action(), None
    except Exception as exc:
        return None, exc


def change_reservation_dates(client, record, payload, selected, doors, visitor, group, requested_level,
                             *, prepare_creation=None, journal=None):
    """Check out, remove the old reservation, create on the same ID, then confirm check-in.

    prepare_creation(original_booking_or_none, payload) validates the Web Client and
    returns a no-arg writer. On recovery after deletion, the old booking is absent.
    """
    validate_edit_request(payload)
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
        complete_payload = preserve_visitor_metadata(payload, details)
        validate_edit_request(complete_payload)
        return _replace(client, record, complete_payload, selected, doors, visitor, requested_level,
                        scope, level, journal, prepare_creation)


def _replace(client, record, payload, selected, doors, visitor, requested_level, scope, level,
             journal, prepare_creation):
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
            valid = (all(plan[k] == v for k, v in scope.items())
                     and plan["stage"] in STAGES
                     and plan["old_checkin"] in {old_checkin, plan.get("new_checkin")} or
                     all(plan[k] == v for k, v in scope.items()) and
                     plan["stage"] in STAGES and old_checkin == plan.get("new_checkin"))
            valid = valid and bool(re.fullmatch(r"\d{1,64}", plan["old_id"])) and bool(
                re.fullmatch(r"\d{1,8}", plan["old_code"]))
        except (OSError, ValueError, TypeError, KeyError):
            valid = False
        if not valid or (original_id and original_id not in (plan["old_id"], plan.get("new_id"))):
            raise HikError("Já existe uma troca pendente. Abra o mesmo visitante e informe as mesmas datas.")
    else:
        if datetime.fromisoformat(end) <= datetime.now(TZ):
            raise EditorFieldError({"exit": "Para criar a nova reserva, informe uma saída que ainda não expirou."})
        if not old_checkin or not old_start or not old_end:
            raise EditorFieldError({"visit": "Selecione a visita anterior que será encerrada."})
        original = find_original_appointment(client, {
            **record, "entry_at": old_start, "exit_at": old_end, "appointment_id": original_id,
        })
        old_period = compare_reservation_period(original, start, end)
        if (not re.fullmatch(r"\d{1,64}", old_period["appointment_id"])
                or not re.fullmatch(r"\d{1,8}", old_period["code"])
                or _id((original.get("visitorInfo") or {}).get("visitorId")) != vid):
            raise EditorFieldError({"appointment": "Confirme a reserva original vinculada ao mesmo visitante."})
        if old_period["state"] == "matched" and _remote_period_matches(old_start, old_end, start, end):
            raise EditorFieldError({"entry": "A visita e a reserva já estão nas datas solicitadas."})
        if parse_access_levels(original) != [level]:
            raise EditorFieldError({"group": "O nível da reserva original difere do nível Visitor selecionado."})
        plan = {**scope, "stage": "prepared", "old_checkin": old_checkin, "old_id": old_period["appointment_id"],
                "old_code": old_period["code"], "old_entry": old_start, "old_exit": old_end,
                "old_booking_entry": old_period["entry_at"], "old_booking_exit": old_period["exit_at"]}
        row = _old_visit(client, plan)
        if _target_booking(client, vid, start, end, plan["old_id"]):
            raise EditorFieldError({"entry": "Já existe outra reserva deste visitante nas novas datas."})
        active = client.find_registration_record(vid, start, end, active_only=True)
        if active and _id(active.get("recordId")) != old_checkin:
            raise EditorFieldError({"visit": "Já existe outra visita ativa do visitante nessas datas."})
        if ((record.get("expect_card") or (row.get("visitorCardInfo") or {}).get("cardNumber"))
                and not visitor.card_number):
            raise EditorFieldError({"card": "Informe a pulseira antes de trocar as datas."})
        if record.get("expect_face") and not visitor.photo_jpeg:
            raise EditorFieldError({"photo": "Informe a foto antes de trocar as datas."})
        # Hold the verified Web Client session in the worker before any checkout.
        create = _prepare_writer(prepare_creation, original, payload)
        _write_json_atomic(journal, plan)

    def save(stage, **extra):
        plan.update(stage=stage, **extra)
        _write_json_atomic(journal, plan)

    if plan["stage"] == "identity_conflict":
        raise HikError("O HikCentral retornou outro Visitor ID ao criar a reserva. Confira os cadastros antes de agir.")
    create = locals().get("create")
    failure = None
    if plan["stage"] == "prepared":
        original = _old_booking(client, plan, require=True)
        if create is None:
            create = _prepare_writer(prepare_creation, original, payload)
        row = _old_visit(client, plan)
        if str(row["visitorStatus"]) == "0":
            save("checkout_inflight")
            _, failure = _write_once(lambda: client.post(CHECKOUT, {"appointRecordId": plan["old_checkin"]}))
        else:
            save("checked_out")
    if plan["stage"] == "checkout_inflight":
        if str(_old_visit(client, plan)["visitorStatus"]) not in CLOSED_STATES:
            raise HikError("Etapa 1: o HikCentral ainda não confirmou o checkout. Confira a visita anterior.") from failure
        save("checked_out")
    if plan["stage"] == "checked_out":
        original = _old_booking(client, plan, require=True)
        if create is None:
            create = _prepare_writer(prepare_creation, original, payload)
        if str(_old_visit(client, plan)["visitorStatus"]) not in CLOSED_STATES:
            raise HikError("Etapa 1: a visita anterior voltou a ficar ativa. Confira o checkout.")
        save("delete_inflight")
        _, failure = _write_once(lambda: client.post(DELETE_BOOKING, {"appointRecordId": plan["old_id"]}))
    if plan["stage"] == "delete_inflight":
        if _old_booking(client, plan):
            raise HikError("Etapa 2: o HikCentral ainda mostra a reserva antiga. Confira a exclusão.") from failure
        save("deleted")
    if plan["stage"] == "deleted":
        if _old_booking(client, plan):
            raise HikError("Etapa 2: a reserva antiga reapareceu. Confira a exclusão.")
        if _target_booking(client, vid, start, end, plan["old_id"]):
            raise HikError("Etapa 3: já existe outra reserva nessas datas. Confira no HikCentral.")
        if create is None:
            create = _prepare_writer(prepare_creation, None, payload)
        save("create_inflight")
        result, failure = _write_once(create)
        if isinstance(result, dict):
            aid, code = _id(result.get("appointment_id")), _id(result.get("code"))
            if (_id(result.get("visitor_id")) != vid or aid == plan["old_id"]
                    or not re.fullmatch(r"\d{1,64}", aid) or not re.fullmatch(r"\d{1,8}", code)):
                save("identity_conflict")
                raise HikError("Etapa 3: o HikCentral devolveu um vínculo diferente para a reserva nova.")
            save("create_inflight", new_id=aid, new_code=code)

    new = (_booking_by_code(client, plan["new_code"], start, end, vid) if plan.get("new_code")
           else _target_booking(client, vid, start, end, plan["old_id"]))
    if not new:
        raise HikError("Etapa 3: a reserva antiga foi excluída e a nova ainda não aparece. "
                       "Confira a reserva no HikCentral antes de repetir.") from failure
    new_id, code = original_appointment_identifier(new), _id(new.get("appointCode"))
    if (new_id == plan["old_id"] or not re.fullmatch(r"\d{1,64}", new_id)
            or not re.fullmatch(r"\d{1,8}", code) or plan.get("new_id", new_id) != new_id):
        raise HikError("Etapa 3: o HikCentral retornou um vínculo divergente para a reserva nova.")
    period = _confirm_new_booking(client, new, start, end, [level])
    if plan["stage"] == "create_inflight":
        save("created", new_id=new_id, new_code=code)

    active = client.find_registration_record(vid, start, end, active_only=True)
    if active and _id(active.get("recordId")) == plan["old_checkin"]:
        raise HikError("Etapa 4: o HikCentral ainda apresenta a visita anterior como ativa.")
    if plan["stage"] == "created" and active:
        save("checked_in", new_checkin=_id(active.get("recordId")), automatic_checkin=True)
    elif plan["stage"] == "created":
        save("checkin_inflight", automatic_checkin=False)
        returned, failure = _write_once(lambda: client.register_existing_visitor(
            vid, payload, selected, appointment_id=new_id))
        if returned:
            save("checkin_inflight", new_checkin=_id(returned))
        active = client.find_registration_record(vid, start, end, active_only=True)
    rid = _id((active or {}).get("recordId"))
    if not rid or rid == plan["old_checkin"] or plan.get("new_checkin", rid) != rid:
        raise HikError("Etapa 4: a reserva nova foi criada, mas o check-in ainda não foi confirmado. "
                       "Confira o visitante no HikCentral antes de repetir.") from failure
    if plan["stage"] == "checkin_inflight":
        save("checked_in", new_checkin=rid)
    _confirmed_edit(client, vid, payload, visitor, group, rid)
    new = _booking_by_code(client, code, start, end, vid)
    if not new or original_appointment_identifier(new) != new_id:
        raise HikError("A reserva nova não apareceu na conferência após o check-in.")
    period = _confirm_new_booking(client, new, start, end, [level])
    remark = (new.get("visitorInfo") or {}).get("remark")
    if remark is None or str(remark).strip() != visitor.reservation.strip():
        raise EditorFieldError({"reservation": "A reserva nova foi criada, mas o apartamento/Remark "
                                "não foi confirmado nela. Confira a reserva no HikCentral."})
    if visitor.photo_jpeg and not same_photo(visitor.photo_jpeg, fetch_visitor_photo(client, vid)):
        raise EditorFieldError({"photo": "A foto no perfil ainda difere da foto informada para a nova visita."})

    if plan["stage"] in {"checked_in", "apply_inflight"}:
        if plan["stage"] != "applied":
            save("apply_inflight")
            current_levels = parse_access_levels(visitor_details(client, vid))
            if not any(str(item.get("id")) == level["id"] and item.get("name") == level["name"]
                       for item in current_levels):
                client.assign_access(vid, level["id"])
            client.apply(vid, doors)
            save("applied")
    if plan["stage"] != "applied":
        raise HikError("Reserva e check-in confirmados, mas o HikCentral não aceitou a solicitação de acesso.")
    history = {"visitor_id": vid, "appointment_id": new_id, "registration_record_id": rid,
               "reservation_code": code, "reservation_entry_at": start, "reservation_exit_at": end,
               "entry_at": start, "exit_at": end, "group": group,
               "first": visitor.first_name, "last": visitor.last_name, "reservation": visitor.reservation,
               "door_ids": list(doors), "access_names": list(requested_level),
               "expect_face": bool(visitor.photo_jpeg), "expect_card": bool(visitor.card_number),
               "checkin_state": "check-in efetuado", "state": "submitted"}
    save_registration(history)
    journal.unlink()
    return {"state": "submitted",
            "message": "O HikCentral recebeu a solicitação de acesso e controla a distribuição.",
            "operation": "replace", "rebooking_completed": True, "operation_completed": True,
            "automatic_checkin": bool(plan.get("automatic_checkin")), "confirmed_appointment_id": new_id,
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
        raise HikError("A reserva anterior mudou durante a troca. Confira no HikCentral.")
    if require and not old:
        raise HikError("A reserva anterior não foi encontrada para confirmar a troca.")
    return old


def _prepare_writer(prepare_creation, old, payload):
    if not callable(prepare_creation):
        raise HikError("Entre no HikCentral para preparar a criação da reserva nova.")
    create = prepare_creation(old, payload)
    if not callable(create):
        raise HikError("O HikCentral não confirmou que pode criar a reserva para o mesmo visitante.")
    return create
