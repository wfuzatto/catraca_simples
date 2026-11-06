"""Atualização segura de visita existente: reserva e check-in confirmados por leitura.

NUNCA cria outra reserva/pessoa, nem reenvia um update de resultado incerto.
Se a resposta de escrita não puder ser conciliada, deixa um journal auditável
e NÃO substitui datas ou IDs do histórico local.
"""
from pathlib import Path
from core import app_dir, _write_json_atomic
from remote_history import find_original_appointment, original_appointment_identifier
from visitor_history import save_registration, set_history_status
from access_receipt import make_access_receipt


def update_existing_visit(client, record: dict, payload: dict,
                          selected: list[dict], doors: list[str],
                          visitor, group: str, requested_level: list[str],
                          *, journal_path: Path | None = None) -> dict:
    vid = str(record.get("visitor_id") or "").strip()
    original_checkin = str(record.get("registration_record_id") or "").strip()
    if not vid or not original_checkin or not selected or not doors:
        raise ValueError("Edição exige Visitor ID, check-in e nível de acesso confirmados.")
    old_id = client.find_existing_registration(
        vid, record["entry_at"], record["exit_at"])
    if str(old_id or "") != original_checkin:
        raise ValueError("Check-in original divergiu. Atualize a lista; não foi enviado update.")

    # A API usa appointRecordId da RESERVA no body de registerment/update.
    # O recordId da consulta de check-ins não deve ser usado nesse campo.
    original_appointment = find_original_appointment(client, record)
    appointment_id = original_appointment_identifier(original_appointment)
    if not appointment_id:
        raise ValueError(
            "A reserva original não retornou appointRecordId. "
            "Edição bloqueada para não romper o vínculo com o check-in.")
    observed = str(record.get("_original_appointment_id") or "").strip()
    if observed and appointment_id != observed:
        raise ValueError("ID da reserva mudou desde a abertura do editor. Atualize a lista.")

    journal = journal_path or (app_dir() / "visitor_edit_inflight.json")
    if journal.exists():
        raise ValueError(
            "Existe edição com resultado incerto. Confira reserva e check-in no "
            "HikCentral antes de liberar nova alteração.")
    new_start = payload["visitStartTime"]
    new_end = payload["visitEndTime"]
    summary = {
        "visitor_id": vid,
        "original_checkin_id": original_checkin,
        "original_appointment_id": appointment_id,
        "old_entry_at": record["entry_at"], "old_exit_at": record["exit_at"],
        "requested_entry_at": new_start, "requested_exit_at": new_end,
    }
    _write_json_atomic(journal, {**summary, "status": "update_inflight"})
    # Exatamente um POST de escrita. Timeout/erro não autoriza reenviar.
    returned_appointment_id = client.update_registered_visitor(
        appointment_id, vid, payload, selected)
    _write_json_atomic(journal, {
        **summary, "status": "update_acknowledged",
        "returned_appointment_id": returned_appointment_id})

    # Leitura remota OBRIGATÓRIA antes de gravar o histórico local, reaplicar
    # níveis ou declarar êxito. Requer check-in e reserva da mesma identidade
    # e da mesma validade. Nenhuma busca por nome ou código de reserva.
    actual_checkin = client.find_existing_registration(
        vid, new_start, new_end)
    if not actual_checkin:
        raise ValueError(
            "O HikCentral respondeu ao update, mas NÃO confirmou o check-in "
            "nas novas datas. Histórico local preservado. Não repita o envio.")
    new_identity = {
        "visitor_id": vid, "entry_at": new_start, "exit_at": new_end,
    }
    updated_appointment = find_original_appointment(client, new_identity)
    actual_appointment = original_appointment_identifier(updated_appointment)
    if not actual_appointment:
        raise ValueError(
            "O check-in existe, mas não foi possível confirmar sua reserva "
            "nas novas datas. Histórico local preservado. Não repita o envio.")
    if returned_appointment_id and returned_appointment_id != actual_appointment:
        raise ValueError(
            "O ID da reserva devolvido pelo update difere da reserva consultada. "
            "Não repita a operação; concilie pelo HikCentral.")

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
        "state": "reaplicação pendente",
        "checkin_state": "confirmado",
    }
    # Só modificar a prova local após correspondência remota dupla.
    save_registration(verified)
    _write_json_atomic(journal, {
        **summary, "status": "link_verified",
        "confirmed_appointment_id": actual_appointment,
        "confirmed_checkin_id": str(actual_checkin)})

    client.apply(vid, doors)
    report = client.delivery_report(
        vid, doors,
        expect_face=bool(visitor.photo_jpeg),
        expect_card=bool(visitor.card_number))
    set_history_status(vid, state=report["state"], checkin_state="confirmado")
    report["access_receipt_saved"] = False
    if str(report.get("state") or "").casefold() == "confirmed":
        receipt = make_access_receipt(
            {**verified, "state": report["state"]},
            level_id=selected[0]["id"], door_ids=doors)
        save_registration({
            **verified, "state": report["state"], "access_receipt": receipt})
        report["access_receipt_saved"] = True
    report["confirmed_appointment_id"] = actual_appointment
    report["confirmed_checkin_id"] = str(actual_checkin)
    journal.unlink(missing_ok=True)
    return report
