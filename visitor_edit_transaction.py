"""Atualização segura de visita existente: reserva e check-in confirmados por leitura.

NUNCA cria outra reserva/pessoa, nem reenvia um update de resultado incerto.
Se a resposta de escrita não puder ser conciliada, deixa um journal auditável
e NÃO substitui datas ou IDs do histórico local.
"""
from pathlib import Path
from core import app_dir, _write_json_atomic, VALE_VISITOR_LEVELS
from remote_history import find_original_appointment, original_appointment_identifier
from visitor_history import save_registration, set_history_status
from access_receipt import make_access_receipt
from hikcentral import HikError


def update_existing_visit(client, record: dict, payload: dict,
                          selected: list[dict], doors: list[str],
                          visitor, group: str, requested_level: list[str],
                          *, journal_path: Path | None = None) -> dict:
    vid = str(record.get("visitor_id") or "").strip()
    original_checkin = str(record.get("registration_record_id") or "").strip()
    if not vid or not original_checkin or not selected or not doors:
        raise ValueError("Edição exige Visitor ID, check-in e nível de acesso confirmados.")

    # Não permitir que o mesmo editor troque um visitante de grupo, mantendo
    # níveis antigos em paralelo. A mudança de grupo exige fluxo específico
    # de remoção do privilégio anterior e concessão do novo.
    original_group = str(record.get("group") or "").strip()
    if not original_group or str(group).strip() != original_group:
        raise ValueError(
            "O grupo do visitante diverge do original. Alteração de grupo "
            "bloqueada até haver migração de permissões segura.")
    approved = VALE_VISITOR_LEVELS.get(original_group)
    if (not approved or list(requested_level) != [approved] or
            len(selected) != 1 or str(selected[0].get("name") or "") != approved):
        raise ValueError("Nível Visitor não corresponde ao grupo original.")
    visitor_entries = payload.get("visitorInfoList") or []
    try:
        requested_group = visitor_entries[0]["VisitorInfo"]["visitorGroupName"]
    except (IndexError, TypeError, KeyError):
        raise ValueError("Payload de edição não contém grupo original verificável.")
    if str(requested_group).strip() != original_group:
        raise ValueError("O payload altera o grupo original; edição bloqueada.")

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
    # Após recusa da OpenAPI, duas consultas somente leitura ajudam a separar
    # uma resposta recusada de uma operação possivelmente persistida, sem
    # jamais concluir automaticamente que é seguro repetir o POST.
    try:
        returned_appointment_id = client.update_registered_visitor(
            appointment_id, vid, payload, selected)
    except HikError as exc:
        observed_old = "consulta indisponível"
        observed_new = "consulta indisponível"
        try:
            observed_old = str(bool(client.find_existing_registration(
                vid, record["entry_at"], record["exit_at"]))).lower()
        except Exception:
            pass
        try:
            observed_new = str(bool(client.find_existing_registration(
                vid, new_start, new_end))).lower()
        except Exception:
            pass
        _write_json_atomic(journal, {
            **summary, "status": "update_refused_or_uncertain",
            "old_checkin_visible": observed_old,
            "requested_checkin_visible": observed_new,
        })
        raise HikError(
            str(exc)
            + "\nConsulta posterior (sem gravar): check-in nas datas antigas = "
            + observed_old + "; nas datas novas = " + observed_new
            + ". O código da API não identifica sozinho a causa da recusa. "
              "Confira o estado da visita e o log protegido do HikCentral. "
              "Não repita o envio antes de conciliar a pendência.") from exc
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

    # A confirmação da DATA não basta: conferir também o grupo remoto.
    # Sem evidência do grupo original, não informar sucesso nem reaplicar
    # acesso, para não deixar Vale e Serra incoerentes.
    from remote_history import visitor_details
    from hikcentral import normalize
    details = visitor_details(client, vid)
    info = details.get("VisitorInfo") or details.get("visitorInfo") or details
    if not isinstance(info, dict):
        raise ValueError("O HikCentral não confirmou o grupo do visitante editado.")
    remote_vid = str(info.get("visitorId") or info.get("ID") or "").strip()
    remote_group = str(
        info.get("visitorGroupName") or info.get("visitorGroup") or "").strip()
    if (remote_vid and remote_vid != vid) or not remote_group or (
            normalize(remote_group) != normalize(original_group)):
        raise ValueError(
            "Grupo do visitante no HikCentral não corresponde ao grupo original. "
            "Histórico preservado; verifique o registro antes de nova alteração.")

    # Repete a etapa de associação usada no cadastro original, mas SOMENTE
    # para o MESMO nível homologado. Não cria permissões de outro grupo.
    client.assign_access(vid, str(selected[0]["id"]))

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
    report["confirmed_group"] = original_group
    journal.unlink(missing_ok=True)
    return report
