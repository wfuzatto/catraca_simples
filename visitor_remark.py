"""Synchronize the visitor's Remark through its linked pending reservation."""
from hashlib import sha256
import json
import re

from core import _write_json_atomic
from edit_errors import EditorFieldError
from edit_journal import select_edit_journal
from hikcentral import HikError
from ordered_reservation_change import _lock_transaction, _write_once
from remote_history import compare_reservation_period, visitor_details
from reservation_replacement import _booking_by_code
from unregistered_reservation import UPDATE_BOOKING, _period, _request
from visitor_profile import validate_visitor_remark


def _visitor_info(client, visitor_id):
    data = visitor_details(client, visitor_id)
    info = data.get("VisitorInfo") or data.get("visitorInfo") or data
    if not isinstance(info, dict) or str(info.get("visitorId") or "") != visitor_id:
        raise EditorFieldError({"remark": "O HikCentral não confirmou o perfil para alterar o Remark."})
    return info


def update_pending_visitor_remark(client, record, remark, *, journal=None):
    """Write and verify Remark for the same reservation code, visitor and period."""
    vid = str(record.get("visitor_id") or "").strip()
    aid = str(record.get("appointment_id") or "").strip()
    code = str(record.get("reservation_code") or "").strip()
    remark = validate_visitor_remark(remark)
    if not (re.fullmatch(r"\d{1,48}", vid) and re.fullmatch(r"\d{1,64}", aid)
            and re.fullmatch(r"\d{1,8}", code)):
        raise EditorFieldError({"remark": "Abra a reserva vinculada para salvar o Remark deste visitante."})
    start, end = _period(record.get("reservation_entry_at") or record.get("entry_at"),
                         record.get("reservation_exit_at") or record.get("exit_at"))
    scope = {"server": str(getattr(client, "url", "")), "visitor_id": vid, "code": code,
             "start": start, "end": end, "remark_digest": sha256(remark.encode("utf-8")).hexdigest(),
             "operation": "visitor_remark"}
    journal = journal or select_edit_journal(f"agenda:{vid}:{code}")

    def checked_reservation():
        booking = _booking_by_code(client, code, start, end, vid)
        if not booking or str(booking.get("appointStatus")) != "0":
            raise EditorFieldError({"remark": "A reserva vinculada não está aguardando check-in."})
        period = compare_reservation_period(booking, start, end)
        if (period["state"] != "matched" or not re.fullmatch(r"\d{1,64}", period["appointment_id"])
                or client.find_registration_record(vid, start, end)):
            raise EditorFieldError({"remark": "As datas ou o check-in da reserva mudaram. Busque novamente."})
        return booking, period

    def matches(booking, profile):
        observed = booking["visitorInfo"]
        return all("remark" in info and str(info["remark"] or "").strip() == remark
                   for info in (observed, profile))

    with _lock_transaction(journal):
        booking, period = checked_reservation()
        profile = _visitor_info(client, vid)
        if journal.exists():
            try:
                pending = json.loads(journal.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise HikError("Não foi possível ler a edição pendente da reserva.") from exc
            if not isinstance(pending, dict) or any(pending.get(key) != value for key, value in scope.items()):
                raise EditorFieldError({"remark": "Há uma edição pendente para esta reserva. Confira o último envio."})
            if matches(booking, profile) and booking.get("accessInfo") == pending.get("accessInfo"):
                journal.unlink()
                return period
            raise EditorFieldError({"remark": "O HikCentral ainda não confirmou o envio anterior do Remark."})
        if period["appointment_id"] != aid:
            raise EditorFieldError({"remark": "A reserva vinculada mudou no HikCentral. Busque novamente."})
        if matches(booking, profile):
            return None
        request = _request(client, booking, vid, start, end, remark, "", None)
        # A simultaneous name edit updates Person Information first. The old
        # booking may still carry the previous name, so keep the verified profile's names.
        visitor = request["visitorInfoList"][0]["VisitorInfo"]
        for field in ("visitorGivenName", "visitorFamilyName"):
            if profile.get(field):
                visitor[field] = profile[field]
        _write_json_atomic(journal, {**scope, "accessInfo": booking.get("accessInfo")})
        _, write_error = _write_once(lambda: client.post(UPDATE_BOOKING, request))
        updated, updated_period = checked_reservation()
        if (not matches(updated, _visitor_info(client, vid)) or
                updated.get("accessInfo") != booking.get("accessInfo")):
            raise EditorFieldError({"remark": "O HikCentral não confirmou o Remark no perfil e na reserva vinculada."}) from write_error
        journal.unlink()
        return updated_period
