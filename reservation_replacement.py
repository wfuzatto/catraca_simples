"""Confirm a replacement made from Visitor Information before removing the old booking."""
from datetime import datetime, timedelta
import json
import re

from core import _write_json_atomic
from edit_errors import EditorFieldError
from edit_journal import select_edit_journal
from hikcentral import HikError
from remote_history import (APPOINTMENTS_PATH, compare_reservation_period, fetch_visitor_photo, find_original_appointment,
                            original_appointment_identifier, parse_access_levels)
from visitor_edit_transaction import _confirmed_edit, update_existing_visit
from visitor_history import save_registration
from visitor_profile import same_photo


DELETE_BOOKING = "/artemis/api/visitor/v1/appointment/single/delete"
APPROVAL_DETAILS = "/artemis/api/visitor/v1/visitorApprovalFlow/details"


def _booking_by_code(client, code, start, end, visitor_id):
    data = client.post(APPOINTMENTS_PATH, {
        "pageNo": 1, "pageSize": 100, "appointCode": code, "appointState": -1,
        "appointStartTime": (datetime.fromisoformat(start) - timedelta(days=1)).isoformat(),
        "appointEndTime": (datetime.fromisoformat(end) + timedelta(days=1)).isoformat(),
    }).get("data") or {}
    rows = data.get("list")
    if rows is None and str(data.get("total")) == "0":
        return None
    if not isinstance(rows, list) or int(data.get("total") or len(rows)) > len(rows):
        raise HikError("A consulta do código da reserva não retornou uma lista completa.")
    found = [row for row in rows if str(row.get("appointCode") or "") == code]
    if not found:
        return None
    if len(found) != 1 or str((found[0].get("visitorInfo") or {}).get("visitorId")) != visitor_id:
        raise HikError("O código da reserva não identifica unicamente o perfil selecionado.")
    return found[0]


def _confirm_new_booking(client, booking, start, end, levels):
    if str(booking.get("appointStatus")) not in {"0", "3"}:
        raise EditorFieldError({"replacement_code": "A reserva nova precisa estar disponível ou registrada."})
    period = compare_reservation_period(booking, start, end)
    if period["state"] != "matched" or parse_access_levels(booking) != levels:
        raise EditorFieldError({"replacement_code": "Confira as datas e o nível original na nova reserva."})
    flow = booking.get("approvalFlowCode")
    if flow:
        approval = client.post(APPROVAL_DETAILS, {"approvalFlowCode": str(flow)}).get("data") or {}
        if str(approval.get("status")) != "1":
            raise EditorFieldError({"replacement_code": "Aguarde a aprovação da reserva nova no HikCentral."})
    return period


def finish_replacement(client, record, code, payload, selected, doors, visitor, group, requested_level,
                       *, journal=None):
    """Use the same visitor and defer deletion until the new visit is confirmed."""
    vid = str(record.get("visitor_id") or "")
    code = code.strip()
    if not re.fullmatch(r"\d{1,8}", code):
        raise EditorFieldError({"replacement_code": "Informe o código numérico da reserva nova."})
    if not vid or len(selected) != 1 or not doors:
        raise HikError("Confirme o visitante e o nível de acesso original.")
    start, end = payload["visitStartTime"], payload["visitEndTime"]
    new = _booking_by_code(client, code, start, end, vid)
    if not new:
        raise EditorFieldError({"replacement_code": "Reserva nova não encontrada nessas datas para este visitante."})
    levels = [{"id": str(level["id"]), "name": level["name"]} for level in selected]
    period = _confirm_new_booking(client, new, start, end, levels)
    new_id = period["appointment_id"]
    if not re.fullmatch(r"\d{1,64}", new_id):
        raise HikError("O HikCentral não retornou o ID da nova reserva.")
    journal = journal or select_edit_journal("reserva:" + vid)
    if journal.exists():
        plan = json.loads(journal.read_text(encoding="utf-8"))
        if (plan["visitor_id"], plan["new_id"], plan["start"], plan["end"]) != (vid, new_id, start, end):
            raise HikError("Há uma troca pendente para este perfil. Confira a mesma reserva e as mesmas datas.")
        if (plan["stage"] not in {"prepared", "visit_inflight", "delete_inflight"}
                or not re.fullmatch(r"\d{1,64}", plan["old_id"]) or plan["old_id"] == new_id):
            raise HikError("A troca pendente contém um vínculo inválido.")
    else:
        original_id = str(record.get("appointment_id") or "")
        if not re.fullmatch(r"\d{1,64}", original_id) or original_id == new_id:
            raise EditorFieldError({"replacement_code": "Selecione uma reserva nova e confirme o vínculo da reserva anterior."})
        original = find_original_appointment(client, record)
        if original_appointment_identifier(original) != original_id:
            raise HikError("A consulta não confirmou a reserva anterior.")
        old_period = compare_reservation_period(original, start, end)
        if not old_period["code"]:
            raise HikError("A consulta não confirmou o código da reserva anterior.")
        plan = {"visitor_id": vid, "new_id": new_id, "start": start, "end": end,
                "old_id": original_id, "old_code": old_period["code"],
                "old_start": old_period["entry_at"], "old_end": old_period["exit_at"],
                "stage": "prepared"}
        with journal.open("x", encoding="utf-8") as f:
            json.dump(plan, f)

    active = client.find_registration_record(vid, start, end, active_only=True)
    if not active:
        if plan["stage"] != "prepared":
            raise HikError("Confira o check-in no HikCentral antes de repetir a troca.")
        _write_json_atomic(journal, {**plan, "stage": "visit_inflight"})
        update_existing_visit(client, {**record, "appointment_id": new_id}, payload,
                              selected, doors, visitor, group, requested_level)
        active = client.find_registration_record(vid, start, end, active_only=True)
    rid = str((active or {}).get("recordId") or "")
    if not rid:
        raise HikError("O HikCentral não confirmou o check-in nas datas da reserva nova.")
    _confirmed_edit(client, vid, payload, visitor, group, rid)
    if visitor.photo_jpeg and not same_photo(visitor.photo_jpeg, fetch_visitor_photo(client, vid)):
        raise EditorFieldError({"photo": "A foto no perfil ainda difere da foto informada para a nova visita."})
    original_checkin = str(record.get("registration_record_id") or "")
    if original_checkin and original_checkin != rid and record.get("entry_at") and record.get("exit_at"):
        if client.find_registration_record(vid, record["entry_at"], record["exit_at"],
                                           active_only=True, record_id=original_checkin):
            raise HikError("O HikCentral ainda mostra outra visita ativa para este visitante.")
    report = client.delivery_report(vid, doors, expect_face=bool(visitor.photo_jpeg),
                                    expect_card=bool(visitor.card_number))
    report.update(confirmed_checkin_id=rid, reservation_period=period, replacement_state="pending_access")
    if report["state"] != "confirmed":
        return report

    old = _booking_by_code(client, plan["old_code"], plan["old_start"], plan["old_end"], vid)
    if old and plan["stage"] == "delete_inflight":
        raise HikError("A exclusão anterior ainda não foi confirmada. Confira o resultado no HikCentral.")
    if old:
        if (original_appointment_identifier(old) != plan["old_id"]
                or compare_reservation_period(old, plan["old_start"], plan["old_end"])["state"] != "matched"):
            raise HikError("A reserva anterior mudou desde a conferência.")
        still_new = _booking_by_code(client, code, start, end, vid)
        if not still_new or original_appointment_identifier(still_new) != new_id:
            raise HikError("A reserva nova mudou antes da exclusão da anterior.")
        _confirm_new_booking(client, still_new, start, end, levels)
        marker = journal.with_name(journal.stem + "." + plan["old_id"] + ".delete")
        try:
            with marker.open("x", encoding="utf-8") as f:
                json.dump(plan, f)
        except FileExistsError as exc:
            raise HikError("A exclusão já foi iniciada. Confira a reserva anterior no HikCentral.") from exc
        _write_json_atomic(journal, {**plan, "stage": "delete_inflight"})
        try:
            client.post(DELETE_BOOKING, {"appointRecordId": plan["old_id"]})
        except HikError:
            # The request may have reached the server. Reconcile by read only.
            pass
    elif plan["stage"] != "delete_inflight":
        raise HikError("A reserva anterior não foi localizada para confirmar a troca.")
    if _booking_by_code(client, plan["old_code"], plan["old_start"], plan["old_end"], vid):
        raise HikError("O HikCentral ainda lista a reserva anterior. Confira antes de reenviar.")
    observed = _booking_by_code(client, code, start, end, vid)
    if not observed or original_appointment_identifier(observed) != new_id:
        raise HikError("A nova reserva não apareceu na conferência após a exclusão.")
    _confirm_new_booking(client, observed, start, end, levels)
    _confirmed_edit(client, vid, payload, visitor, group, rid)
    if visitor.photo_jpeg and not same_photo(visitor.photo_jpeg, fetch_visitor_photo(client, vid)):
        raise HikError("A foto precisa ser conferida após a troca.")
    after = client.delivery_report(vid, doors, expect_face=bool(visitor.photo_jpeg),
                                   expect_card=bool(visitor.card_number))
    if after["state"] != "confirmed":
        return {**report, **after, "replacement_state": "pending_after_delete"}
    save_registration({
        "visitor_id": vid, "appointment_id": new_id, "reservation_code": code,
        "reservation_entry_at": start, "reservation_exit_at": end,
        "registration_record_id": rid, "entry_at": start, "exit_at": end,
        "first": visitor.first_name, "last": visitor.last_name, "group": group,
        "reservation": visitor.reservation, "checkin_state": "check-in efetuado",
        "state": report["state"], "door_ids": list(doors), "access_names": list(requested_level),
        "expect_face": bool(visitor.photo_jpeg), "expect_card": bool(visitor.card_number),
    })
    journal.unlink()
    return {**report, "replacement_state": "confirmed"}
