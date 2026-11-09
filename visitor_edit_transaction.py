"""Edição do check-in ativo e seus dados, confirmados por leitura.

NUNCA cria outra reserva/pessoa, nem reenvia um update de resultado incerto.
Se a resposta de escrita não puder ser conciliada, deixa um journal auditável
e NÃO substitui datas ou IDs do histórico local.
"""
from pathlib import Path
from datetime import datetime
import re
import hashlib
import json
import time
from core import _write_json_atomic, VALE_VISITOR_LEVELS, TZ
from remote_history import visitor_details, reservation_period_report
from edit_journal import select_edit_journal
from edit_errors import EditorFieldError
from edit_request import validate_edit_request, preserve_visitor_metadata
from visitor_history import save_registration, set_history_status
from hikcentral import HikError


def _confirmed_edit(client, vid, payload, visitor, group, returned_id):
    """Confere os campos gravados, sem escrever ou criar outra reserva."""
    row = client.find_registration_record(
        vid, payload["visitStartTime"], payload["visitEndTime"], active_only=True, record_id=returned_id)
    if not row or str(row.get("visitorStatus")) != "0":
        raise ValueError("O HikCentral NÃO confirmou o check-in nas novas datas.")
    actual = str(row.get("recordId") or "")
    if returned_id and actual != returned_id:
        raise ValueError("O ID devolvido pela edição difere do check-in consultado.")
    base = row.get("visitorBaseInfo") or {}
    from hikcentral import normalize
    if normalize(str(base.get("visitorGroup") or "")) != normalize(group):
        raise ValueError("Grupo do visitante no HikCentral difere do grupo original.")
    details = visitor_details(client, vid)
    info = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if not isinstance(info, dict) or str(info.get("visitorId") or "") != vid:
        raise ValueError("O HikCentral não confirmou a identidade do visitante editado.")
    # Esta instalação retorna o grupo no check-in, não em VisitorInfo.
    detail_group = str(info.get("visitorGroupName") or info.get("visitorGroup") or "")
    if detail_group and normalize(detail_group) != normalize(group):
        raise ValueError("Grupo do visitante no HikCentral difere do grupo original.")
    expected = {"visitorGivenName": visitor.first_name,
                "visitorFamilyName": visitor.last_name, "remark": visitor.reservation}
    for key, value in expected.items():
        if key not in info or str(info[key] or "").strip() != str(value).strip():
            raise ValueError("O HikCentral não confirmou nome, sobrenome e reserva/apartamento gravados.")
    expected_name = " ".join((visitor.first_name.strip(), visitor.last_name.strip()))
    if base.get("fullName") and " ".join(str(base["fullName"]).split()) != " ".join(expected_name.split()):
        raise ValueError("O nome no histórico de check-in ainda não reflete a edição.")
    if visitor.card_number:
        card = (row.get("visitorCardInfo") or {}).get("cardNumber")
        if str(card or "").strip() != str(visitor.card_number).strip():
            raise ValueError("O HikCentral não confirmou a pulseira gravada.")
    return actual


def _readback(client, vid, payload, visitor, group, returned_id):
    for attempt in range(3):
        if attempt:
            time.sleep(attempt)
        try:
            return _confirmed_edit(client, vid, payload, visitor, group, returned_id)
        except (ValueError, HikError) as exc:
            last_error = exc
    raise ValueError(str(last_error) + " Histórico local preservado; não reenvie a alteração.") from last_error


def update_existing_visit(client, record: dict, payload: dict,
                          selected: list[dict], doors: list[str],
                          visitor, group: str, requested_level: list[str],
                          *, journal_path: Path | None = None) -> dict:
    validate_edit_request(payload)
    vid = str(record.get("visitor_id") or "").strip()
    if not vid or not selected or not doors:
        raise EditorFieldError({"visit": "Selecione o cadastro do visitante e confirme o nível de acesso."})
    record = dict(record)
    chosen = record.get("_selected_checkin_record")
    if isinstance(chosen, dict):
        base = chosen.get("visitorBaseInfo") or {}
        if str(base.get("visitorId") or "") != vid:
            raise EditorFieldError({"visit": "A visita selecionada pertence a outro visitante."})
        record.update(registration_record_id=chosen.get("recordId"),
                      entry_at=base.get("visitStartTime"), exit_at=base.get("visitEndTime"))
    original_checkin = str(record.get("_selected_checkin_id") or record.get("registration_record_id") or "")
    original_group = str(record.get("_original_group") or record.get("group") or "").strip()
    allow_group_migration = bool(record.get("_allow_group_migration"))
    requested_group_change = bool(original_group and
                                  str(group).strip().casefold() != original_group.casefold())
    group_changed = bool(allow_group_migration and
                         (not original_group or requested_group_change))
    if record.get("_operator_selected_group") and not original_group and not allow_group_migration:
        original_group = group
        group_changed = False
    if (not original_group and not allow_group_migration) or (requested_group_change and not allow_group_migration):
        raise EditorFieldError({"group": "O grupo do visitante diverge do original. Alteração de grupo exige conferir o grupo atual."},
                               choices={"group": [original_group] if original_group else []})
    original_level_ids = (record.get("_original_access_level_ids") or
                          record.get("access_level_ids") or [])
    if group_changed and (len(original_level_ids) != 1 or not original_level_ids[0]):
        raise EditorFieldError({"group": "O nível Visitor original não foi confirmado; atualize a consulta antes de trocar o grupo."})
    approved = VALE_VISITOR_LEVELS.get(group)
    if (not approved or list(requested_level) != [approved] or len(selected) != 1 or
            str(selected[0].get("name") or "") != approved):
        raise EditorFieldError({"group": "Selecione o nível Visitor correspondente ao grupo."})
    if payload["visitorInfoList"][0]["VisitorInfo"]["visitorGroupName"].strip() != group:
        raise EditorFieldError({"group": "O payload altera o grupo original; selecione o grupo correto."})

    journal = journal_path or select_edit_journal(vid)
    new_start, new_end = payload["visitStartTime"], payload["visitEndTime"]
    requested_hash = hashlib.sha256(json.dumps({"payload": payload, "group": group},
        sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    actual_appointment = str(record.get("appointment_id") or record.get("_original_appointment_id") or "")
    summary = {"visitor_id": vid, "original_checkin_id": original_checkin,
        "original_group": original_group,
        "original_level_id": str(original_level_ids[0]) if original_level_ids else "",
        "old_entry_at": record.get("entry_at", ""), "old_exit_at": record.get("exit_at", ""),
        "requested_entry_at": new_start, "requested_exit_at": new_end,
        "requested_fields_sha256": requested_hash}
    if journal.exists():
        pending = json.loads(journal.read_text(encoding="utf-8"))
        if (pending.get("visitor_id") != vid or pending.get("requested_fields_sha256") != requested_hash or
                pending.get("status") not in ("update_acknowledged", "link_verified")):
            raise EditorFieldError({"visit": "Existe edição com resultado incerto para este visitante. Confira a pendência antes de outro envio."})
        returned_record_id = str(pending.get("returned_record_id") or "")
        operation = pending.get("operation", "update")
        actual_appointment = pending.get("appointment_id", actual_appointment)
    else:
        try:
            old = client.find_registration_record(vid, record.get("entry_at") or new_start,
                record.get("exit_at") or new_end, active_only=False, record_id=original_checkin)
        except HikError as exc:
            raise EditorFieldError({"visit": "Selecione qual visita deste visitante deseja alterar."},
                choices={"visit": getattr(exc, "candidates", [])}) from exc
        if not old or (original_checkin and str(old.get("recordId") or "") != original_checkin):
            raise EditorFieldError({"visit": "O check-in original mudou. Selecione a visita atual para continuar."})
        original_checkin = str(old["recordId"])
        summary["original_checkin_id"] = original_checkin
        from hikcentral import normalize
        live_group = str((old.get("visitorBaseInfo") or {}).get("visitorGroup") or "")
        if live_group and original_group and normalize(live_group) != normalize(original_group):
            raise EditorFieldError({"group": "O grupo atual mudou no HikCentral: " + live_group}, choices={"group": [live_group]})
        remote_state = str(old.get("visitorStatus", ""))
        if remote_state not in ("0", "1", "2", "3", "4"):
            operation = record.get("_operator_edit_operation")
            if operation not in ("update", "renew"):
                raise EditorFieldError({"operation": "Selecione atualizar a visita existente ou renovar a visita, pois o estado não retornou."},
                    choices={"operation": ["Atualizar visita existente", "Renovar visita"]})
        else:
            operation = "update" if remote_state == "0" else "renew"
        if operation == "renew":
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", actual_appointment):
                raise EditorFieldError({"appointment": "Informe ou selecione a reserva vinculada no HikCentral para renovar esta visita encerrada."})
            if datetime.fromisoformat(new_end) <= datetime.now(TZ):
                raise EditorFieldError({"exit": "Para renovar a visita encerrada, informe uma data de saída que ainda não expirou."})
        try:
            details = visitor_details(client, vid)
        except HikError:
            details = {}
        info = details.get("VisitorInfo") or details.get("visitorInfo") or details
        if isinstance(info, dict) and info.get("visitorId") and str(info["visitorId"]) != vid:
            raise EditorFieldError({"visit": "Não foi possível confirmar o visitante. Selecione o cadastro correto."})
        detail_group = str(info.get("visitorGroupName") or info.get("visitorGroup") or "") if isinstance(info, dict) else ""
        if not live_group and detail_group and original_group and normalize(detail_group) != normalize(original_group):
            raise EditorFieldError({"group": "O grupo atual mudou no HikCentral: " + detail_group}, choices={"group": [detail_group]})
        complete_payload = preserve_visitor_metadata(payload, details)
        validate_edit_request(complete_payload)
        summary.update(operation=operation, appointment_id=actual_appointment)
        _write_json_atomic(journal, {**summary, "status": "update_inflight"})
        try:
            if operation == "renew":
                returned_record_id = client.register_existing_visitor(vid, complete_payload, selected,
                    appointment_id=actual_appointment)
            else:
                returned_record_id = client.update_registered_visitor(original_checkin, vid, complete_payload, selected)
        except HikError as exc:
            observed = {}
            for label, start, end in (("old", record.get("entry_at") or new_start, record.get("exit_at") or new_end),
                                     ("requested", new_start, new_end)):
                try:
                    observed[label] = str(bool(client.find_existing_registration(vid, start, end))).lower()
                except Exception:
                    observed[label] = "consulta indisponível"
            _write_json_atomic(journal, {**summary, "status": "update_refused_or_uncertain",
                "old_checkin_visible": observed["old"], "requested_checkin_visible": observed["requested"]})
            raise HikError(str(exc) + "\nConsulta posterior (sem gravar): check-in nas datas antigas = "
                + observed["old"] + "; nas datas novas = " + observed["requested"] + ". Nenhum segundo envio foi realizado.") from exc
        _write_json_atomic(journal, {**summary, "status": "update_acknowledged", "returned_record_id": returned_record_id})

    # HikCentral may reset Person.orgIndexCode on check-in update. Restore
    # the approved group BEFORE checking the result; the old order failed
    # the readback first and left the visitor in default Visitors.
    from visitor_profile import set_visitor_group
    set_visitor_group(client, vid, group)
    actual_checkin = _readback(client, vid, payload, visitor, group, returned_record_id)
    # A successful check-in update does not prove that the original booking
    # changed. Read its independent period and report any real divergence.
    reservation_period = reservation_period_report(
        client, {**record, "appointment_id": actual_appointment}, new_start, new_end)

    # Atribui o nível aprovado para o grupo escolhido antes de remover o antigo.
    client.assign_access(vid, str(selected[0]["id"]))
    if group_changed and str(original_level_ids[0]) != str(selected[0]["id"]):
        client.remove_access(vid, str(original_level_ids[0]))

    verified = {
        **record,
        "first": visitor.first_name, "last": visitor.last_name,
        "group": group, "reservation": visitor.reservation,
        "entry_at": new_start, "exit_at": new_end,
        "registration_record_id": str(actual_checkin),
        "appointment_id": actual_appointment,
        "access_names": list(requested_level),
        "access_level_ids": [str(selected[0]["id"])],
        "door_ids": list(doors),
        "expect_face": bool(visitor.photo_jpeg),
        "expect_card": bool(visitor.card_number),
        "state": "aplicação solicitada",
        # The readback above proved visitorStatus=0. Persist the actual state,
        # so opening this same record again does not demand a hidden operation.
        "checkin_state": "check-in efetuado",
    }
    if reservation_period["state"] in ("matched", "different"):
        verified.update(reservation_entry_at=reservation_period["entry_at"],
                        reservation_exit_at=reservation_period["exit_at"],
                        reservation_code=reservation_period["code"])
    # Só modificar a prova local após correspondência remota dupla.
    save_registration(verified)
    _write_json_atomic(journal, {
        **summary, "status": "link_verified", "operation": operation, "appointment_id": actual_appointment,
        "returned_record_id": returned_record_id,
        "confirmed_checkin_id": str(actual_checkin)})

    client.apply(vid, doors)
    # No later write is allowed to revert the selected visitor group.
    set_visitor_group(client, vid, group)
    set_history_status(vid, state="submitted", checkin_state="check-in efetuado")
    journal.unlink(missing_ok=True)
    return {
        "state": "submitted",
        "message": "O HikCentral recebeu a solicitação de acesso e controla a distribuição.",
        "operation": operation,
        "operation_completed": True,
        "access_receipt_saved": False,
        "confirmed_appointment_id": actual_appointment,
        "confirmed_checkin_id": str(actual_checkin),
        "confirmed_group": group,
        "reservation_period": reservation_period,
    }
