"""Edição do check-in ativo e seus dados, confirmados por leitura.

NUNCA cria outra reserva/pessoa, nem reenvia um update de resultado incerto.
Se a resposta de escrita não puder ser conciliada, deixa um journal auditável
e NÃO substitui datas ou IDs do histórico local.
"""
from pathlib import Path
import hashlib
import json
import time
from core import _write_json_atomic, VALE_VISITOR_LEVELS
from remote_history import visitor_details
from edit_journal import select_edit_journal
from visitor_history import save_registration, set_history_status
from access_receipt import make_access_receipt
from hikcentral import HikError


def _confirmed_edit(client, vid, payload, visitor, group, returned_id):
    """Confere os campos gravados, sem escrever ou criar outra reserva."""
    row = client.find_registration_record(
        vid, payload["visitStartTime"], payload["visitEndTime"], active_only=True)
    if not row:
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

    # O ID retornado pelo registerment é o recordId da consulta de check-ins.
    # A reserva feita ANTES do check-in tem outro ID e pode sair da listagem.
    journal = journal_path or select_edit_journal(vid)
    new_start, new_end = payload["visitStartTime"], payload["visitEndTime"]
    requested_hash = hashlib.sha256(json.dumps({
        "payload": payload, "group": group,
    }, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    summary = {
        "visitor_id": vid, "original_checkin_id": original_checkin,
        "old_entry_at": record["entry_at"], "old_exit_at": record["exit_at"],
        "requested_entry_at": new_start, "requested_exit_at": new_end,
        "requested_fields_sha256": requested_hash,
    }
    if journal.exists():
        pending = json.loads(journal.read_text(encoding="utf-8"))
        if (pending.get("visitor_id") != vid or
                pending.get("requested_fields_sha256") != requested_hash or
                pending.get("status") not in ("update_acknowledged", "link_verified")):
            raise ValueError(
                "Existe edição com resultado incerto para este visitante. "
                "Confira os dados no HikCentral antes de outra alteração.")
        # Repetir Salvar após uma leitura atrasada retoma SOMENTE consultas.
        returned_record_id = str(pending.get("returned_record_id") or "")
    else:
        old = client.find_registration_record(
            vid, record["entry_at"], record["exit_at"], active_only=True)
        if not old or str(old.get("recordId") or "") != original_checkin:
            raise ValueError("Check-in original divergiu ou foi encerrado. Pesquise novamente.")
        from hikcentral import normalize
        if normalize(str((old.get("visitorBaseInfo") or {}).get("visitorGroup") or "")) != normalize(group):
            raise ValueError("Grupo original divergiu. Pesquise novamente antes de editar.")
        _write_json_atomic(journal, {**summary, "status": "update_inflight"})
        try:
            returned_record_id = client.update_registered_visitor(
                original_checkin, vid, payload, selected)
        except HikError as exc:
            observed = {}
            for label, start, end in (("old", record["entry_at"], record["exit_at"]),
                                      ("requested", new_start, new_end)):
                try:
                    observed[label] = str(bool(client.find_existing_registration(vid, start, end))).lower()
                except Exception:
                    observed[label] = "consulta indisponível"
            _write_json_atomic(journal, {
                **summary, "status": "update_refused_or_uncertain",
                "old_checkin_visible": observed["old"],
                "requested_checkin_visible": observed["requested"],
            })
            raise HikError(
                str(exc) + "\nConsulta posterior (sem gravar): check-in nas datas antigas = "
                + observed["old"] + "; nas datas novas = " + observed["requested"]
                + ". Nenhum segundo envio foi realizado.") from exc
        _write_json_atomic(journal, {
            **summary, "status": "update_acknowledged",
            "returned_record_id": returned_record_id})

    actual_checkin = _readback(client, vid, payload, visitor, group, returned_record_id)
    # O booking ID anterior continua separado do ID de check-in editado.
    actual_appointment = str(record.get("appointment_id") or
                             record.get("_original_appointment_id") or "")

    # Repete a etapa de associação usada no cadastro original, mas SOMENTE
    # para o MESMO nível homologado. Não cria permissões de outro grupo.
    client.assign_access(vid, str(selected[0]["id"]))

    verified = {
        **record,
        "first": visitor.first_name, "last": visitor.last_name,
        "group": group, "reservation": visitor.reservation,
        "entry_at": new_start, "exit_at": new_end,
        "registration_record_id": str(actual_checkin),
        "access_names": list(requested_level),
        "access_level_ids": [str(selected[0]["id"])],
        "door_ids": list(doors),
        "expect_face": bool(visitor.photo_jpeg),
        "expect_card": bool(visitor.card_number),
        "state": "reaplicação pendente",
        "checkin_state": "confirmado",
    }
    # Só modificar a prova local após correspondência remota dupla.
    save_registration(verified)
    _write_json_atomic(journal, {
        **summary, "status": "link_verified",
        "returned_record_id": returned_record_id,
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
