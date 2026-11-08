"""Consulta central de check-ins do HikCentral, sem cache de fotos/NFC.

A OpenAPI é a fonte de verdade para registros remotos. Campos ausentes não
são inferidos a partir das configurações locais de outra recepção.
"""
from __future__ import annotations

import base64
import binascii
import io
import re
from datetime import datetime, timedelta
from PIL import Image
from core import TZ
from hikcentral import HikError

CHECKINS_PATH = "/artemis/api/visitor/v1/register/getVistorRegisterRecord"
PHOTO_PATH = "/artemis/api/visitor/v1/appointment/downloadpicture"
DETAIL_PATH = "/artemis/api/visitor/v1/visitor/single/visitorinfo"
DIRECTORY_PATH = "/artemis/api/visitor/v1/visitor/visitorInfo"
APPOINTMENTS_PATH = "/artemis/api/visitor/v1/appointment/appointmentlist"
PAGE_SIZE = 100
MAX_PAGES = 100
STATUSES = {
    "0": "check-in efetuado", "1": "check-out", "2": "saída automática",
    "3": "saída no terminal", "4": "saída em atraso",
}


def search_window(start_text: str, end_text: str):
    try:
        start = datetime.strptime(start_text.strip(), "%d/%m/%Y").replace(tzinfo=TZ)
        end = datetime.strptime(end_text.strip(), "%d/%m/%Y").replace(tzinfo=TZ)
    except ValueError as exc:
        raise ValueError("Informe as datas no formato DD/MM/AAAA.") from exc
    if end < start:
        raise ValueError("A data final não pode ser anterior à data inicial.")
    if (end-start).days > 31:
        raise ValueError("Consulte no máximo 32 dias por vez; ajuste o período.")
    # Inclui todo o último dia civil do filtro.
    return start.isoformat(timespec="seconds"), (end+timedelta(days=1)).isoformat(timespec="seconds")


def normalize_checkin(row: dict):
    if not isinstance(row, dict):
        return None
    base = row.get("visitorBaseInfo") or {}
    if not isinstance(base, dict):
        return None
    vid = str(base.get("visitorId") or "").strip()
    if not vid or len(vid) > 64:
        return None
    full_name = str(base.get("fullName") or "").strip()
    first, _, last = full_name.partition(" ")
    # Check-in pode trazer o nível explicitamente, mesmo quando a reserva
    # omite accessInfo. Nunca inferir nível a partir do nome do grupo.
    levels = parse_access_levels(row)
    start = str(base.get("visitStartTime") or "")
    end = str(base.get("visitEndTime") or "")
    # Nunca inventar período e chaves de reenvio se o serviço os omitir.
    return {
        "visitor_id": vid,
        "registration_record_id": str(row.get("recordId") or ""),
        "appointment_id": _checkin_appointment_id(row),
        "first": first, "last": last, "full_name": full_name,
        "group": str(base.get("visitorGroup") or ""),
        "reservation": str(base.get("remark") or ""),
        "entry_at": start, "exit_at": end,
        # registerTime é a data real do check-in, diferente da validade.
        "registered_at": str(row.get("registerTime") or ""),
        "checkin_state": STATUSES.get(str(row.get("visitorStatus")), "não informado"),
        "state": "consultar na catraca",
        "photo_url": str(base.get("smallPhotoUrl") or ""),
        "card_number": str((row.get("visitorCardInfo") or {}).get("cardNumber") or ""),
        "origin": "HikCentral",
        "door_ids": [], "access_names": [x["name"] for x in levels],
        "access_level_ids": [x["id"] for x in levels],
        "expect_face": None, "expect_card": None,
    }


def _checkin_appointment_id(row):
    """Use explicit reservation links only. recordId identifies the check-in."""
    ids = {
        str(info.get(key)).strip()
        for info in (row, row.get("visitorBaseInfo") or {})
        if isinstance(info, dict)
        for key in ("appointId", "appointID")
        if info.get(key)
    }
    value = next(iter(ids)) if len(ids) == 1 else ""
    return value if len(value) <= 64 else ""


def add_local_booking_reference(record, local):
    """Recover the booking reference from the same check-in, not the latest visit."""
    local_id = str(local.get("appointment_id") or "")
    remote_id = str(record.get("appointment_id") or record.get("_original_appointment_id") or "")
    checkin_id = str(record.get("registration_record_id") or "")
    if (not local_id or not checkin_id
            or str(record.get("visitor_id") or "") != str(local.get("visitor_id") or "")
            or checkin_id != str(local.get("registration_record_id") or "")
            or (remote_id and remote_id != local_id)):
        return record
    result = {**record, "appointment_id": local_id}
    for field in ("reservation_entry_at", "reservation_exit_at", "reservation_code"):
        if local.get(field) and not result.get(field):
            result[field] = local[field]
    return result


def filter_local_history_by_entry_date(rows: list[dict], start_iso: str,
                                       end_iso: str, name: str = "") -> list[dict]:
    """Aplica ao histórico offline o mesmo recorte de data e nome da tela.

    A data considerada é `entry_at` (início válido), NUNCA
    `registered_at` (momento do cadastro). Data final é exclusiva,
    conforme search_window(); ausência de validade não permite inferências.
    """
    start = datetime.fromisoformat(start_iso).astimezone(TZ)
    end = datetime.fromisoformat(end_iso).astimezone(TZ)
    needle = _normalized_person_name(name)
    matching = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        try:
            validity = datetime.fromisoformat(
                str(item.get("entry_at") or "")).astimezone(TZ)
        except (TypeError, ValueError):
            continue
        if not (start <= validity < end):
            continue
        label = item.get("full_name") or " ".join(filter(
            None, (item.get("first"), item.get("last"))))
        if needle and needle not in _normalized_person_name(label):
            continue
        matching.append(item)
    return matching


def _history_query(page, page_size, name):
    if not 1 <= page <= MAX_PAGES or not 1 <= page_size <= 500:
        raise ValueError("Página inválida.")
    name = name.strip()
    if len(name) > 64 or (name and not re.fullmatch(r"[\w À-ÿ.\-']+", name)):
        raise ValueError("Pesquisa por nome inválida (máximo 64 caracteres).")
    server_size = min(page_size, PAGE_SIZE)
    server_first_page = (page - 1) * page_size // server_size + 1
    request_count = (page_size + server_size - 1) // server_size
    return name, server_size, range(server_first_page, server_first_page + request_count)


def fetch_checkins(client, start_iso: str, end_iso: str, page: int = 1,
                   name: str = "", page_size: int = PAGE_SIZE):
    """Paginate the selected batch using safe server requests of up to 100.

    Selecting 200/500 in the UI must return 200/500 records even on older
    HikCentral versions that reject pageSize >100. The logical page can be
    multiple adjacent server pages sorted by visitingTime.
    """
    name, server_size, server_pages = _history_query(page, page_size, name)
    rows, seen = [], set()
    total = None
    for server_page in server_pages:
        if total is not None and (server_page - 1) * server_size >= total:
            break
        payload = {
            "pageNo": server_page, "pageSize": server_size,
            "visitStartTime": start_iso, "visitEndTime": end_iso,
            "sortField": "visitingTime", "orderType": "1",
        }
        if name:
            payload["visitorName"] = name
        response = client.post(CHECKINS_PATH, payload)
        data = response.get("data") or {}
        if not isinstance(data, dict) or "totalNum" not in data:
            raise HikError("HikCentral não retornou lista paginada de check-ins.")
        try:
            current_total = int(data["totalNum"])
        except (TypeError, ValueError) as exc:
            raise HikError("Total inválido no resultado do HikCentral.") from exc
        if current_total < 0:
            raise HikError("Total inválido no resultado do HikCentral.")
        if total is None:
            total = current_total
        # Total que muda entre duas páginas altera a ordenação/continuidade.
        elif current_total != total:
            raise HikError(
                "O total de check-ins mudou durante a consulta paginada; "
                "atualize a lista para evitar dados incompletos.")
        items = data.get("list")
        if items is None and current_total == 0:
            items = []
        if not isinstance(items, list):
            raise HikError("HikCentral não retornou lista paginada de check-ins.")
        for item in items:
            record = normalize_checkin(item)
            if record:
                identity = (record["visitor_id"], record["registration_record_id"],
                            record["entry_at"], record["exit_at"])
                if identity not in seen:
                    rows.append(record)
                    seen.add(identity)
    total = total if total is not None else 0
    return {
        "rows": rows, "total": total, "page": page,
        "page_size": page_size,
        "has_next": page < MAX_PAGES and (page * page_size < total),
    }


def fetch_unchecked_reservations(client, start_iso, end_iso, page=1, name="", page_size=PAGE_SIZE):
    """List actual reservations still in Reserved state, independently of check-ins."""
    name, server_size, server_pages = _history_query(page, page_size, name)
    rows = {}
    total = None
    for server_page in server_pages:
        if total is not None and (server_page - 1) * server_size >= total:
            break
        params = {
            "pageNo": server_page, "pageSize": server_size, "appointState": 0,
            "appointStartTime": start_iso, "appointEndTime": end_iso,
            "sortField": "VisitingTime", "orderType": 1,
        }
        if name:
            params["visitorName"] = name
        data = client.post(APPOINTMENTS_PATH, params).get("data") or {}
        try:
            current_total = int(data["total"])
            if current_total < 0:
                raise ValueError()
        except (TypeError, ValueError, KeyError) as exc:
            raise HikError("HikCentral não retornou a paginação de reservas.") from exc
        if total is not None and current_total != total:
            raise HikError("O total de reservas mudou durante a consulta. Clique em Buscar novamente.")
        total = current_total
        items = data.get("list")
        if items is None and total == 0:
            items = []
        if not isinstance(items, list):
            raise HikError("HikCentral não retornou uma lista de reservas.")
        for item in items:
            row = _normalize_unchecked_reservation(item)
            if row:
                rows[row["appointment_id"]] = row
    return {"rows": list(rows.values()), "total": total or 0, "page": page,
            "page_size": page_size, "has_next": page < MAX_PAGES and page * page_size < (total or 0)}


def _normalize_unchecked_reservation(item):
    if not isinstance(item, dict) or str(item.get("appointStatus")) != "0":
        return None
    info = item.get("visitorInfo")
    if not isinstance(info, dict):
        return None
    vid = str(info.get("visitorId") or "").strip()
    if not vid or len(vid) > 64:
        return None
    try:
        period = compare_reservation_period(item, item.get("appointStartTime"), item.get("appointEndTime"))
    except (TypeError, ValueError):
        return None
    if not period["appointment_id"] or not period["code"]:
        return None
    first = str(info.get("visitorGivenName") or info.get("firstName") or "").strip()
    last = str(info.get("visitorFamilyName") or info.get("lastName") or "").strip()
    full_name = str(info.get("visitorName") or info.get("fullName")
                    or " ".join(filter(None, (first, last)))).strip()
    if not first and not last:
        first, _, last = full_name.partition(" ")
    levels = parse_access_levels(item)
    return {
        "_reservation_only": True, "_reservation_period": period,
        "visitor_id": vid, "appointment_id": period["appointment_id"],
        "reservation_code": period["code"], "registration_record_id": "",
        "entry_at": period["entry_at"], "exit_at": period["exit_at"],
        "reservation_entry_at": period["entry_at"], "reservation_exit_at": period["exit_at"],
        "first": first, "last": last, "full_name": full_name,
        "group": str(info.get("visitorGroupName") or info.get("visitorGroup") or ""),
        "reservation": str(info.get("remark") or ""), "checkin_state": "sem check-in",
        "origin": "Visitor Reservation",
        "access_names": [x["name"] for x in levels], "access_level_ids": [x["id"] for x in levels],
    }



def _visitor_directory_rows(data):
    """Extrai pessoas de formatos conhecidos sem supor que houve check-in."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    options = [
        data.get("list"), data.get("visitorInfoList"),
        data.get("VisitorInfoList"), data.get("visitorList"),
        data.get("VisitorList"), data.get("VisitorInfo"),
    ]
    for container in options:
        if isinstance(container, list):
            return container
        if isinstance(container, dict):
            for key in ("list", "VisitorInfo", "Visitor", "visitor", "visitorInfo"):
                if isinstance(container.get(key), list):
                    return container[key]
            if any(key in container for key in ("visitorId", "ID", "visitorGivenName", "visitorFamilyName")):
                return [container]
    if any(key in data for key in ("visitorId", "ID", "visitorGivenName", "visitorFamilyName")):
        return [data]
    return []


def _normalized_person_name(value):
    import unicodedata
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return " ".join("".join(
        char for char in normalized if not unicodedata.combining(char)
    ).split())


def _normalize_directory_person(item, name):
    if not isinstance(item, dict):
        return None
    data = item.get("VisitorInfo") or item.get("visitorInfo") or item
    if not isinstance(data, dict):
        return None
    base = data.get("BaseInfo") or data.get("baseInfo") or data
    if not isinstance(base, dict):
        base = data
    vid = str(data.get("visitorId") or data.get("ID") or data.get("id")
              or base.get("visitorId") or base.get("ID") or "").strip()
    if not vid or len(vid) > 64:
        return None
    first = str(data.get("visitorGivenName") or base.get("visitorGivenName")
                or data.get("firstName") or "").strip()
    last = str(data.get("visitorFamilyName") or base.get("visitorFamilyName")
               or data.get("lastName") or "").strip()
    full = str(base.get("fullName") or data.get("fullName") or
               " ".join(filter(None, (first, last)))).strip()
    if not first and full:
        first, _, last = full.partition(" ")
    if not full or _normalized_person_name(name) not in _normalized_person_name(full):
        return None
    group = str(data.get("visitorGroupName") or base.get("visitorGroupName")
                or data.get("visitorGroup") or "").strip()
    return {
        "visitor_id": vid, "registration_record_id": "",
        "first": first, "last": last, "full_name": full,
        "group": group, "reservation": str(data.get("remark") or ""),
        "entry_at": "", "exit_at": "", "registered_at": "",
        "checkin_state": "sem check-in no período",
        "state": "não consultado",
        "photo_url": str(data.get("smallPhotoUrl") or base.get("smallPhotoUrl") or ""),
        "card_number": "", "origin": "VisitorInformation",
        "door_ids": [], "access_names": [],
        "expect_face": None, "expect_card": None,
    }


def search_visitor_directory(client, name, *, page=1, page_size=100):
    """Busca nomes em Visitor Information, independentemente da data da visita.

    A resposta é *somente consulta*. Nunca oferece permissões inferidas,
    nem usa o ID de outra pessoa por coincidência parcial de nome.
    """
    name = str(name or "").strip()
    if len(name) < 3 or len(name) > 128:
        raise ValueError("Informe pelo menos 3 caracteres e no máximo 128.")
    if not 1 <= page <= 100 or not 1 <= page_size <= 500:
        raise ValueError("Página inválida.")
    response = client.post(DIRECTORY_PATH, {
        "pageNo": page, "pageSize": page_size,
        "searchCriteria": {"personName": name},
    })
    raw = response.get("data")
    entries = _visitor_directory_rows(raw)
    if not isinstance(raw, (dict, list)):
        raise HikError("Visitor Information não retornou dados utilizáveis.")
    if not entries and isinstance(raw, dict):
        count = raw.get("total") or raw.get("totalNum") or 0
        if count:
            raise HikError("Visitor Information retornou um formato de lista desconhecido.")
    unique = {}
    for item in entries:
        row = _normalize_directory_person(item, name)
        if row:
            unique[row["visitor_id"]] = row
    return list(unique.values())



def parse_access_levels(data):
    """Extrai níveis IDENTIFICADOS por ID e nome em campos documentados.

    HikCentral retorna accessInfo tanto no objeto de reserva quanto no
    visitorInfo, e algumas versões retornam accessLevelList em formatos
    encapsulados. Não tenta adivinhar grupo nem usa mapeamento local.
    """
    if not isinstance(data, dict):
        return []
    candidates = [data]
    for key in ("visitorInfo", "VisitorInfo", "visitorBaseInfo", "VisitorBaseInfo"):
        child = data.get(key)
        if isinstance(child, dict):
            candidates.append(child)
    unique = {}
    for container in candidates:
        access = container.get("accessInfo") or container.get("AccessInfo")
        if not isinstance(access, dict):
            continue
        rows = access.get("accessLevelList") or access.get("AccessLevelList") or []
        if isinstance(rows, dict):
            rows = rows.get("list") or rows.get("AccessLevel") or rows.get("accessLevel") or []
            if isinstance(rows, dict):
                rows = [rows]
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            level = item.get("accessLevel") or item.get("AccessLevel") or item
            if not isinstance(level, dict):
                continue
            base = level.get("baseInfo") or level.get("BaseInfo") or level.get("baseinfo") or {}
            if not isinstance(base, dict):
                base = {}
            name = str(base.get("name") or level.get("name") or "").strip()
            level_id = str(level.get("id") or level.get("ID") or "").strip()
            if name and level_id:
                if level_id in unique and unique[level_id] != name:
                    raise HikError("OpenAPI retornou nomes conflitantes para o mesmo nível de acesso.")
                unique[level_id] = name
    return [{"id": i, "name": n} for i, n in unique.items()]



def _same_visit(appointment, record):
    if not isinstance(appointment, dict):
        return False
    if _appointment_visitor_id(appointment) != record["visitor_id"]:
        return False
    try:
        start = datetime.fromisoformat(str(appointment.get("appointStartTime") or ""))
        end = datetime.fromisoformat(str(appointment.get("appointEndTime") or ""))
        actual_start = datetime.fromisoformat(record["entry_at"])
        actual_end = datetime.fromisoformat(record["exit_at"])
        return (start.tzinfo is not None and end.tzinfo is not None and
                start == actual_start and end == actual_end)
    except (ValueError, TypeError, KeyError):
        return False


def _appointment_visitor_info(appointment):
    """Read visitor data from the casing variants returned by HikCentral."""
    if not isinstance(appointment, dict):
        return {}
    for key in ("visitorInfo", "VisitorInfo", "visitorBaseInfo", "VisitorBaseInfo"):
        info = appointment.get(key)
        if isinstance(info, dict):
            return info
    return {}


def _appointment_visitor_id(appointment):
    info = _appointment_visitor_info(appointment)
    for key in ("visitorId", "visitorID", "VisitorId", "VisitorID", "ID"):
        value = str(info.get(key) or "").strip()
        if value:
            return value
    return ""


def original_appointment_identifier(appointment: dict) -> str:
    """Extrai o identificador da RESERVA; não confundir com recordId de check-in.

    appointRecordId é o campo documentado para a edição de check-in.
    appointID/appointId são aceitos para as versões que os retornam na
    listagem. Quando os campos existirem e divergirem, falhar fechado sem
    escolher um ID arbitrário.
    """
    if not isinstance(appointment, dict):
        return ""
    identifiers = {
        str(appointment.get(key) or "").strip()
        for key in ("appointRecordId", "appointID", "appointId")
        if str(appointment.get(key) or "").strip()
    }
    if len(identifiers) > 1:
        raise HikError("Reserva retornou identificadores divergentes; edição bloqueada.")
    value = next(iter(identifiers), "")
    if len(value) > 64:
        raise HikError("ID de reserva inválido; edição bloqueada.")
    return value


def find_original_appointment(client, record, *, max_pages=10):
    """Verify an explicit booking ID or match a check-in to a unique reservation.

    A check-in edit may change its period while the original booking retains
    the old dates. Where the periods differ, a sole overlapping Registered
    booking for the same visitor and group can still match, including after
    checkout. Other candidates need an explicit selection.
    """
    if not record.get("visitor_id") or not record.get("entry_at") or not record.get("exit_at"):
        raise HikError("Visitante e período são necessários para localizar a reserva original.")
    try:
        start = datetime.fromisoformat(record["entry_at"])
        end = datetime.fromisoformat(record["exit_at"])
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("intervalo")
    except ValueError as exc:
        raise HikError("Datas do registro são inválidas; nenhuma edição autorizada.") from exc
    known_id = str(record.get("appointment_id") or record.get("_original_appointment_id") or "")
    query_start, query_end = start, end
    for key in ("reservation_entry_at", "reservation_exit_at"):
        try:
            reference = datetime.fromisoformat(str(record.get(key) or ""))
            if reference.tzinfo is not None:
                query_start, query_end = min(query_start, reference), max(query_end, reference)
        except (TypeError, ValueError):
            pass
    candidates = {}
    for page in range(1, max_pages + 1):
        payload = {
            "pageNo": page, "pageSize": 100,
            "appointStartTime": (query_start - timedelta(days=1)).isoformat(timespec="seconds"),
            "appointEndTime": (query_end + timedelta(days=1)).isoformat(timespec="seconds"),
            "appointState": -1, "sortField": "VisitingTime", "orderType": 1,
        }
        data = client.post(APPOINTMENTS_PATH, payload).get("data") or {}
        if not isinstance(data, dict):
            raise HikError("A consulta de reservas não retornou lista válida.")
        rows = data.get("list")
        if rows is None and str(data.get("total")) == "0":
            rows = []
        if not isinstance(rows, list):
            raise HikError("A consulta de reservas não retornou lista válida.")
        for entry in rows:
            if _appointment_visitor_id(entry) != str(record["visitor_id"]):
                continue
            candidate_id = original_appointment_identifier(entry)
            if not candidate_id:
                continue
            if candidate_id in candidates and candidates[candidate_id] != entry:
                raise HikError("A reserva mudou durante a consulta. Pesquise novamente.")
            candidates[candidate_id] = entry
        total = int(data.get("total") or 0)
        if total > page * 100 and len(rows) < 100:
            raise HikError("A consulta de reservas retornou uma página incompleta. Pesquise novamente.")
        if len(rows) < 100 or (total and page * 100 >= total):
            break
    else:
        raise HikError("Há reservas demais para validar a edição com segurança.")
    if known_id and known_id in candidates:
        return candidates[known_id]
    if not known_id and len(candidates) == 1:
        entry = next(iter(candidates.values()))
        if _same_visit(entry, record):
            return entry
        booking_group = str(_appointment_visitor_info(entry).get("visitorGroupName") or
                            _appointment_visitor_info(entry).get("visitorGroup") or "")
        same_group = (not booking_group or
                      _normalized_person_name(booking_group) ==
                      _normalized_person_name(record.get("group")))
        # A unique reservation can still be edited after check-out. The prior
        # active-check-in condition incorrectly blocked the same reservation.
        if same_group and str(entry.get("appointStatus")) == "3":
            try:
                period = compare_reservation_period(entry, record["entry_at"], record["exit_at"])
                if (datetime.fromisoformat(period["entry_at"]) < end
                        and datetime.fromisoformat(period["exit_at"]) > start):
                    return entry
            except (TypeError, ValueError):
                pass

    if candidates:
        message = "Selecione a reserva correta deste visitante pelo código e período."
    else:
        message = "Não foi possível associar a visita a uma reserva única no HikCentral. Confira o vínculo da reserva."
    error = HikError(message)
    error.fields = {"appointment": message}
    error.choices = {"appointment": list(candidates.values())}
    raise error


def compare_reservation_period(appointment, start_iso, end_iso):
    """Compare dates read from a reservation with the current check-in period."""
    start = datetime.fromisoformat(str(appointment.get("appointStartTime") or ""))
    end = datetime.fromisoformat(str(appointment.get("appointEndTime") or ""))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("Período da reserva inválido.")
    matches = start == datetime.fromisoformat(start_iso) and end == datetime.fromisoformat(end_iso)
    return {"state": "matched" if matches else "different",
            "appointment_id": original_appointment_identifier(appointment),
            "code": str(appointment.get("appointCode") or ""),
            "entry_at": start.isoformat(timespec="seconds"),
            "exit_at": end.isoformat(timespec="seconds")}


def reservation_period_report(client, record, start_iso, end_iso):
    """Read the linked booking independently; never infer that an edit synced it."""
    if not (record.get("appointment_id") or record.get("_original_appointment_id")):
        return {"state": "unavailable", "message": "Reserva vinculada não identificada."}
    try:
        appointment = find_original_appointment(client, record)
        return compare_reservation_period(appointment, start_iso, end_iso)
    except (HikError, ValueError, TypeError, KeyError):
        return {"state": "unavailable", "message": "Datas da reserva original não confirmadas pela consulta."}


def enrich_checkins_with_reservation_periods(client, rows, local_rows, start_iso, end_iso):
    """Attach live reservation dates to check-ins using a verified reservation link.

    The check-in's visitStartTime/visitEndTime remain intact for editing and
    passage lookup. Display dates come from the linked appointment record.
    Local metadata is used only to recover an appointment ID when its
    registration_record_id matches the exact remote check-in. When the API
    omits that link, a sole same-visitor/group reservation overlapping the
    visit may supply dates and the edit ID.
    """
    local_by_visitor = {str(row.get("visitor_id") or ""): row
                        for row in local_rows if isinstance(row, dict)}
    linked = []
    target_ids = set()
    bounds = []
    for row in rows:
        link = add_local_booking_reference(dict(row), local_by_visitor.get(str(row.get("visitor_id") or ""), {}))
        aid = str(link.get("appointment_id") or link.get("_original_appointment_id") or "").strip()
        if aid:
            target_ids.add(aid)
        linked.append((row, link, aid))
        for field in ("entry_at", "exit_at", "reservation_entry_at", "reservation_exit_at"):
            try:
                value = datetime.fromisoformat(str(link.get(field) or ""))
                if value.tzinfo is not None:
                    bounds.append(value)
            except (TypeError, ValueError):
                pass

    appointments = {}
    by_visitor = {}
    query_error = False
    if linked:
        try:
            target_visitors = {aid: str(link.get("visitor_id") or "")
                               for _row, link, aid in linked if aid}
            target_visitor_ids = {str(link.get("visitor_id") or "")
                                  for _row, link, _aid in linked}
            target_visitor_ids.discard("")
            query_start = min([datetime.fromisoformat(start_iso), *bounds]) - timedelta(days=1)
            query_end = max([datetime.fromisoformat(end_iso), *bounds]) + timedelta(days=1)
            for page in range(1, 11):
                params = {
                    "pageNo": page, "pageSize": 100, "appointState": -1,
                    "appointStartTime": query_start.isoformat(timespec="seconds"),
                    "appointEndTime": query_end.isoformat(timespec="seconds"),
                    "sortField": "VisitingTime", "orderType": 1,
                }
                data = client.post(APPOINTMENTS_PATH, params).get("data") or {}
                if not isinstance(data, dict):
                    raise HikError("A consulta de reservas não retornou dados legíveis.")
                items = data.get("list")
                try:
                    total = int(data["total"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise HikError("A consulta de reservas não retornou o total de registros.") from exc
                if items is None and total == 0:
                    items = []
                if not isinstance(items, list):
                    raise HikError("A consulta de reservas não retornou uma lista válida.")
                for appointment in items:
                    if not isinstance(appointment, dict):
                        continue
                    visitor_id = _appointment_visitor_id(appointment)
                    if visitor_id not in target_visitor_ids:
                        continue
                    aid = original_appointment_identifier(appointment)
                    if not aid or (aid in target_ids and target_visitors.get(aid) != visitor_id):
                        continue
                    if aid in appointments and appointments[aid] != appointment:
                        raise HikError("A mesma reserva retornou dados divergentes.")
                    appointments[aid] = appointment
                    by_visitor.setdefault(visitor_id, {})[aid] = appointment
                if not items or len(items) < 100 or page * 100 >= total:
                    break
        except (HikError, TypeError, ValueError, KeyError):
            query_error = True

    for row, link, aid in linked:
        appointment = appointments.get(aid) if aid else None
        if appointment:
            if _appointment_visitor_id(appointment) != str(row.get("visitor_id") or ""):
                appointment = None
        if not appointment and aid:
            try:
                appointment = find_original_appointment(client, link)
            except (HikError, ValueError, TypeError, KeyError):
                appointment = None
        if not appointment and not aid:
            candidates = list(by_visitor.get(str(row.get("visitor_id") or ""), {}).values())
            if len(candidates) == 1:
                candidate = candidates[0]
                candidate_info = _appointment_visitor_info(candidate)
                candidate_group = str(candidate_info.get("visitorGroupName") or
                                      candidate_info.get("visitorGroup") or "")
                row_group = str(row.get("group") or "")
                same_group = (not candidate_group or not row_group or
                              _normalized_person_name(candidate_group) == _normalized_person_name(row_group))
                try:
                    start = datetime.fromisoformat(str(candidate.get("appointStartTime") or ""))
                    end = datetime.fromisoformat(str(candidate.get("appointEndTime") or ""))
                    visit_start = datetime.fromisoformat(str(row.get("entry_at") or ""))
                    visit_end = datetime.fromisoformat(str(row.get("exit_at") or ""))
                    overlaps = start <= visit_end and end >= visit_start
                    if (end > start and same_group and
                            (_same_visit(candidate, row) or
                             (overlaps and str(candidate.get("appointStatus")) == "3"))):
                        appointment = candidate
                except (TypeError, ValueError):
                    pass
        period = None
        if appointment:
            try:
                period = compare_reservation_period(appointment, row.get("entry_at"), row.get("exit_at"))
            except (ValueError, TypeError, KeyError):
                period = None
        if period:
            row.update(reservation_entry_at=period["entry_at"],
                       reservation_exit_at=period["exit_at"], reservation_code=period["code"],
                       appointment_id=period["appointment_id"], _reservation_period=period)
        else:
            row.update(reservation_entry_at="", reservation_exit_at="", reservation_code="",
                       _reservation_period={"state": "unavailable"},
                       _reservation_lookup_error=bool(query_error or aid))
    return rows


def reservation_period_notice(period, start_iso, end_iso):
    """User-facing dates with their actual source, including a real mismatch."""
    if not isinstance(period, dict) or not period.get("state"):
        return ""
    visit = "Visita (Check-In/Out): " + history_time(start_iso) + " a " + history_time(end_iso) + "."
    if period.get("state") not in ("matched", "different"):
        return visit + "\nDatas da reserva original: não confirmadas."
    booking = "Reserva original" + (" " + period["code"] if period.get("code") else "")
    booking += ": " + history_time(period["entry_at"]) + " a " + history_time(period["exit_at"]) + "."
    mismatch = "\nAs datas da visita e da reserva são diferentes no HikCentral." if period["state"] == "different" else ""
    return visit + "\n" + booking + mismatch


def resolve_remote_edit_record(client, record):
    """Verifica o mesmo Visitor ID/check-in e uma autorização para editar.

    Caminho normal: o nível original está comprovado na reserva, no check-in
    ou nos dados do visitante. Caminho de recuperação: a OpenAPI omitiu todos
    os níveis, mas o grupo tem UM nível Visitor homologado e confirmado por
    consulta ao servidor. Nesse caso, a edição exige aceite explícito do
    operador, pois o nível será REAPLICADO, não apenas preservado.
    """
    from core import VALE_VISITOR_LEVELS, verify_visitor_access_plan
    from hikcentral import normalize

    visitor_id = str(record.get("visitor_id") or "")
    checkin_id = str(record.get("registration_record_id") or "")
    if not visitor_id or not checkin_id:
        raise HikError("Visitor ID e ID do check-in são necessários para editar.")
    original = client.find_registration_record(
        visitor_id, record["entry_at"], record["exit_at"], active_only=False, record_id=checkin_id)
    if (not isinstance(original, dict) or
            str(original.get("recordId") or "") != checkin_id):
        raise HikError("O check-in original mudou ou não é único; edição bloqueada.")

    data = visitor_details(client, visitor_id)
    detail_info = data.get("VisitorInfo") or data.get("visitorInfo") or data
    if not isinstance(detail_info, dict):
        raise HikError("Detalhes do visitante não retornaram um objeto válido.")
    details_id = str(detail_info.get("visitorId") or detail_info.get("ID") or "")
    if details_id and details_id != visitor_id:
        raise HikError("O Visitor ID do detalhe não corresponde ao selecionado.")
    group = str(record.get("group") or "")
    detail_group = str(detail_info.get("visitorGroupName") or
                       detail_info.get("visitorGroup") or "")
    if detail_group and normalize(detail_group) != normalize(group):
        raise HikError("O grupo atual difere do registro de check-in; edição bloqueada.")

    appointment_levels = []
    appointment_error = ""
    appointment_id = ""
    appointment_choices = []
    try:
        remote_appointment_id = _checkin_appointment_id(original)
        record = {**record, "checkin_state": STATUSES.get(
            str(original.get("visitorStatus")), record.get("checkin_state"))}
        if remote_appointment_id and remote_appointment_id != record.get("appointment_id"):
            record.update(appointment_id=remote_appointment_id, reservation_entry_at="",
                          reservation_exit_at="", reservation_code="")
        appointment = find_original_appointment(client, record)
        appt_info = _appointment_visitor_info(appointment)
        appt_group = str(appt_info.get("visitorGroupName") or "")
        if appt_group and normalize(appt_group) != normalize(group):
            raise HikError("Grupo da reserva diverge do check-in.")
        appointment_levels = parse_access_levels(appointment)
        appointment_id = original_appointment_identifier(appointment)
    except HikError as exc:
        appointment_error = str(exc)
        appointment_choices = getattr(exc, "choices", {}).get("appointment", [])

    # A ausência de accessInfo em uma fonte não prova ausência de permissão.
    sources = [("reserva", appointment_levels),
               ("Visitor Information", parse_access_levels(data))]
    record_ids = record.get("access_level_ids") or []
    record_names = record.get("access_names") or []
    if record_ids and record_names and len(record_ids) == len(record_names):
        sources.append(("check-in", [
            {"id": str(i), "name": str(n)}
            for i, n in zip(record_ids, record_names)]))
    levels = {}
    source_names = []
    for source, entries in sources:
        if not entries:
            continue
        if len(entries) != 1:
            raise HikError(
                "O HikCentral retornou múltiplos níveis para esta pessoa. "
                "Não é seguro substituir suas permissões automaticamente.")
        level = entries[0]
        level_id, name = str(level["id"]), str(level["name"])
        if levels and (level_id, normalize(name)) != next(iter(levels)):
            raise HikError(
                "Reserva, check-in e Visitor Information discordam sobre o nível. "
                "Edição bloqueada até conferir as permissões no HikCentral.")
        levels[(level_id, normalize(name))] = level
        source_names.append(source)

    requires_consent = False
    if levels:
        expected = next(iter(levels.values()))
        name = expected["name"]
        source = ", ".join(source_names)
    else:
        # Não se trata de confirmar acesso ORIGINAL: este modo permite
        # REAPLICAR uma única configuração de acesso, com ciência do operador.
        # Nunca escolher níveis de funcionários, grupos desconhecidos ou
        # inventar níveis que não apareceram em /privilege/group?type=2.
        name = VALE_VISITOR_LEVELS.get(group)
        if not name:
            raise HikError(
                "A reserva não confirmou um único nível Visitor e o grupo não "
                "possui um mapeamento homologado para recuperação.")
        requires_consent = True
        source = "nível homologado do grupo — exige confirmação"
        expected = None
    approved_name = VALE_VISITOR_LEVELS.get(group)
    if approved_name and normalize(name) != normalize(approved_name):
        raise HikError(
            "O nível existente difere do nível Visitor homologado para este grupo. "
            "Não é seguro alterar o acesso sem revisão no HikCentral.")
    try:
        selected, doors = client.resolve_levels([name])
        verify_visitor_access_plan(selected, doors, [name])
    except Exception as exc:
        if requires_consent:
            raise HikError(
                "A reserva não confirmou um único nível Visitor e o nível "
                "homologado do grupo não pôde ser validado no HikCentral.") from exc
        raise
    if expected is not None and str(selected[0]["id"]) != str(expected["id"]):
        raise HikError("O ID do nível não corresponde ao nível original do visitante.")
    if record_names and len(record_names) == 1 and normalize(record_names[0]) != normalize(name):
        raise HikError("O nível do check-in difere do nível validado; edição bloqueada.")
    if record_ids and expected is not None and [str(x) for x in record_ids] != [str(expected["id"])]:
        raise HikError("O ID do nível anterior diverge do nível validado.")
    if record.get("door_ids") and set(record["door_ids"]) != set(doors):
        raise HikError("Pontos autorizados anteriores divergentes; edição bloqueada.")

    receipt = None
    if requires_consent:
        # O endpoint do histórico não comprova o nível original. Um recibo
        # LOCAL DPAPI de atualização aceita + entrega confirmada, para ESTE
        # ID de check-in e exatamente o mesmo plano, permite dispensar
        # a confirmação repetida por sete dias. Não é prova de que outra
        # estação não alterou as permissões desde então.
        try:
            from visitor_history import get_history
            from access_receipt import matching_access_receipt
            local = get_history(visitor_id) or {}
            candidate = local.get("access_receipt")
            if matching_access_receipt(
                    candidate, record, level_id=selected[0]["id"],
                    level_name=name, door_ids=doors):
                receipt = candidate
                requires_consent = False
                source = "última reaplicação confirmada nesta estação"
        except (OSError, ValueError, RuntimeError, ImportError):
            # DPAPI ausente ou de outro usuário Windows não pode servir como
            # evidência; manter confirmação explícita.
            pass
    result = dict(record)
    result.update(
        access_names=[name], door_ids=doors,
        access_level_ids=[str(selected[0]["id"])],
        _remote_verified=True, _level_verified_from=source,
        _requires_access_confirmation=requires_consent,
        _access_receipt_confirmed_at=(
            receipt.get("confirmed_at") if receipt else ""),
        _access_assurance_type=("local_acknowledged" if receipt else (
            "remote_level" if expected is not None else "needs_consent")))
    result["_original_checkin_id"] = checkin_id
    result["_update_record_id"] = checkin_id
    state = str(original.get("visitorStatus", ""))
    result["_edit_operation"] = "update" if state == "0" else "renew" if state in ("1", "2", "3", "4") else ""
    result["_original_appointment_id"] = appointment_id
    result["_appointment_lookup_error"] = appointment_error
    result["_appointment_choices"] = appointment_choices
    result["_reservation_period"] = {"state": "unavailable"}
    if appointment_id:
        try:
            period = compare_reservation_period(appointment, record["entry_at"], record["exit_at"])
            result.update(_reservation_period=period, appointment_id=appointment_id,
                          reservation_entry_at=period["entry_at"],
                          reservation_exit_at=period["exit_at"], reservation_code=period["code"])
        except (HikError, TypeError, ValueError):
            pass
    return result, data



def enrich_checkins_with_appointment_levels(client, rows, start_iso, end_iso, *,
                                          max_pages=10):
    """Busca níveis REAIS na lista de reservas; sem inferência pelo grupo.

    Se o endpoint não responder, a listagem de check-ins permanece disponível
    com níveis ausentes, mas a edição do registro não é autorizada.
    """
    if not rows:
        return rows
    pending = [dict(row) for row in rows]
    for page in range(1, max_pages + 1):
        resp = client.post(APPOINTMENTS_PATH, {
            "pageNo": page, "pageSize": 499,
            "appointStartTime": start_iso, "appointEndTime": end_iso,
            "appointState": -1, "sortField": "VisitingTime", "orderType": 1,
        })
        data = resp.get("data") or {}
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise HikError("A consulta de reservas não retornou dados legíveis.")
        for appointment in data["list"]:
            for row in pending:
                if _same_visit(appointment, row):
                    levels = parse_access_levels(appointment)
                    if levels:
                        # Nunca marcar como validado sem exclusividade.
                        row.setdefault("_level_matches", []).append(levels)
        total = int(data.get("total") or 0)
        if len(data["list"]) < 499 or (total and page * 499 >= total):
            break
    for row in pending:
        matches = row.pop("_level_matches", [])
        if len(matches) == 1:
            row["access_names"] = [x["name"] for x in matches[0]]
            row["access_level_ids"] = [x["id"] for x in matches[0]]
    return pending


def history_time(value):
    """Exibir a hora original com segundos; fuso local quando conhecido."""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is not None:
            dt = dt.astimezone(TZ)
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return "—"


def visitor_details(client, visitor_id: str):
    if not visitor_id or len(visitor_id) > 64:
        raise ValueError("Visitor ID inválido.")
    result = client.post(DETAIL_PATH, {"visitorId": visitor_id}).get("data") or {}
    if not isinstance(result, dict):
        raise HikError("Dados do visitante retornados em formato inesperado.")
    return result


def decode_photo_response(value):
    """Somente JPEG/PNG válidos e pequenos; nunca persiste a foto em arquivo."""
    if isinstance(value, dict):
        value = (value.get("data") or value.get("picData") or
                 value.get("picture") or value.get("photo") or "")
    if not isinstance(value, str):
        raise HikError("HikCentral não retornou uma imagem utilizável.")
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    if value.startswith("data:image/jpeg;base64,"):
        b64 = value.split(",", 1)[1]
    elif value.startswith("data:image/png;base64,"):
        b64 = value.split(",", 1)[1]
    elif re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", value):
        b64 = value
    else:
        raise HikError("Resposta de foto fora do formato JPEG/PNG Base64.")
    if len(b64) > 2_800_000:
        raise HikError("Imagem retornada ultrapassa o limite permitido.")
    try:
        # O HikCentral pode inserir quebras de linha na codificação Base64.
        binary = base64.b64decode(re.sub(r"\s+", "", b64), validate=True)
        if len(binary) > 2_000_000 or len(binary) < 4:
            raise ValueError("Tamanho inválido")
        if not (binary.startswith(b"\xff\xd8\xff") or binary.startswith(b"\x89PNG\r\n\x1a\n")):
            raise ValueError("Formato inválido")
        with Image.open(io.BytesIO(binary)) as img:
            if img.format not in ("JPEG", "PNG") or img.width*img.height > 16_000_000:
                raise ValueError("Imagem não suportada")
            img.verify()
    except (binascii.Error, ValueError, OSError, Image.DecompressionBombError) as exc:
        raise HikError("Imagem do HikCentral inválida ou incompatível.") from exc
    return binary


def _photo_references(details):
    """Referências retornadas pelo HikCentral, nunca URLs buscadas pelo cliente."""
    if not isinstance(details, dict):
        return []
    nodes = [details]
    for key in ("VisitorInfo", "visitorInfo", "visitorPhoto", "VisitorPhoto",
                "baseInfo", "BaseInfo"):
        value = details.get(key)
        if isinstance(value, dict):
            nodes.append(value)
            nested = value.get("visitorPhoto") or value.get("VisitorPhoto")
            if isinstance(nested, dict):
                nodes.append(nested)
    refs = []
    for node in nodes:
        for field in ("smallPhotoUrl", "bigPhotoUrl", "picUrl", "photoUrl"):
            val = node.get(field)
            if isinstance(val, str) and 0 < len(val.strip()) <= 256 and val.strip() not in refs:
                refs.append(val.strip())
    return refs[:4]


def fetch_visitor_photo(client, visitor_id: str, pic_url: str = "", *,
                        details=None):
    """Tenta referências reais e os tipos suportados, sem enviar imagem externa.

    A API do HikCentral pode rejeitar a foto com código interno 128 mesmo com
    HTTP 200. Isso não significa que o visitante não possui uma foto no Web
    Client. O editor permitirá capturar outra foto se a OpenAPI não a fornecer.
    """
    if not visitor_id or len(visitor_id) > 64:
        raise ValueError("Visitor ID inválido.")
    pic_url = (pic_url or "").strip()
    if len(pic_url) > 256:
        raise HikError("Referência de foto inválida.")
    refs = []
    if pic_url:
        refs.append(pic_url)
    for item in _photo_references(details):
        if item not in refs:
            refs.append(item)
    errors = []
    # O picUrl é exigido na API documentada. Se não houver referência, a
    # tentativa legada sem picUrl é somente a última alternativa.
    candidates = []
    for item in refs[:3]:
        candidates.append((item, "0"))
    if refs:
        candidates.append((refs[0], "1"))
    if not candidates:
        candidates.append(("", "0"))
    for ref, pic_type in candidates[:4]:
        request = {"visitorId": visitor_id, "picType": pic_type}
        if ref:
            request["picUrl"] = ref
        try:
            response = client.post(PHOTO_PATH, request, allow_image_data=True)
            return decode_photo_response(response.get("data"))
        except (HikError, ValueError) as exc:
            errors.append(str(exc))
    detail = errors[0] if errors else "Nenhuma referência válida encontrada."
    raise HikError("Não foi possível recuperar a foto pela OpenAPI. "
                   "A imagem pode existir no Web Client. "
                   "É possível selecionar uma foto nova ou usar a câmera para "
                   "substituí-la conscientemente. " + detail)
