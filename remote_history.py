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
    start = str(base.get("visitStartTime") or "")
    end = str(base.get("visitEndTime") or "")
    # Nunca inventar período e chaves de reenvio se o serviço os omitir.
    return {
        "visitor_id": vid,
        "registration_record_id": str(row.get("recordId") or ""),
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
        "door_ids": [], "access_names": [],
        "expect_face": None, "expect_card": None,
    }


def fetch_checkins(client, start_iso: str, end_iso: str, page: int = 1,
                   name: str = "", page_size: int = PAGE_SIZE):
    if not 1 <= page <= MAX_PAGES or not 1 <= page_size < 500:
        raise ValueError("Página inválida.")
    name = name.strip()
    if len(name) > 64 or (name and not re.fullmatch(r"[\w À-ÿ.\-']+", name)):
        raise ValueError("Pesquisa por nome inválida (máximo 64 caracteres).")
    payload = {
        "pageNo": page, "pageSize": page_size, "visitStartTime": start_iso,
        "visitEndTime": end_iso, "sortField": "visitingTime", "orderType": "1",
    }
    if name:
        payload["visitorName"] = name
    response = client.post(CHECKINS_PATH, payload)
    data = response.get("data") or {}
    if not isinstance(data, dict) or "totalNum" not in data:
        raise HikError("HikCentral não retornou lista paginada de check-ins.")
    try:
        total = int(data["totalNum"])
    except (TypeError, ValueError) as exc:
        raise HikError("Total inválido no resultado do HikCentral.") from exc
    # Apenas uma resposta explicitamente vazia pode omitir o campo list.
    # Não mascarar falhas/alterações da OpenAPI como "0 visitantes".
    items = data.get("list")
    if items is None and total == 0:
        items = []
    if not isinstance(items, list):
        raise HikError("HikCentral não retornou lista paginada de check-ins.")
    if total < 0:
        raise HikError("Total inválido no resultado do HikCentral.")
    rows = []
    seen = set()
    for item in items:
        record = normalize_checkin(item)
        if record:
            # Um visitante pode ter vários check-ins em períodos diferentes.
            identity = (record["visitor_id"], record["registration_record_id"],
                        record["entry_at"], record["exit_at"])
            if identity not in seen:
                rows.append(record)
                seen.add(identity)
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "has_next": page < MAX_PAGES and (page*page_size < total if total else len(items) == page_size)}



def parse_access_levels(data):
    """Extrai somente níveis explicitamente retornados pela OpenAPI.

    A listagem de check-ins não inclui níveis; nas reservas eles ficam em
    visitorInfo.accessInfo.accessLevelList / accessInfo.accessLevelList.
    """
    if not isinstance(data, dict):
        return []
    info = data.get("visitorInfo") or data.get("VisitorInfo") or {}
    access = (info.get("accessInfo") if isinstance(info, dict) else None) or data.get("accessInfo") or {}
    if not isinstance(access, dict):
        return []
    rows = access.get("accessLevelList")
    if not isinstance(rows, list):
        return []
    items = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        level = item.get("accessLevel") or item.get("AccessLevel") or item
        if not isinstance(level, dict):
            continue
        base = level.get("baseInfo") or {}
        name = str(base.get("name") or level.get("name") or "").strip()
        level_id = str(level.get("id") or "").strip()
        if name and level_id and not any(p["id"] == level_id for p in items):
            items.append({"id": level_id, "name": name})
    return items


def _same_visit(appointment, record):
    if not isinstance(appointment, dict):
        return False
    info = appointment.get("visitorInfo") or {}
    if not isinstance(info, dict) or str(info.get("visitorId") or "") != record["visitor_id"]:
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


def find_original_appointment(client, record, *, max_pages=10):
    """Localiza a reserva do MESMO visitante e intervalo; ambiguidades bloqueiam edição."""
    if not record.get("visitor_id") or not record.get("entry_at") or not record.get("exit_at"):
        raise HikError("Visitante e período são necessários para localizar a reserva original.")
    try:
        start = datetime.fromisoformat(record["entry_at"])
        end = datetime.fromisoformat(record["exit_at"])
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("intervalo")
    except ValueError as exc:
        raise HikError("Datas do registro são inválidas; nenhuma edição autorizada.") from exc
    matches = []
    for page in range(1, max_pages + 1):
        payload = {
            "pageNo": page, "pageSize": 100,
            "appointStartTime": (start - timedelta(days=1)).isoformat(timespec="seconds"),
            "appointEndTime": (end + timedelta(days=1)).isoformat(timespec="seconds"),
            "appointState": -1, "sortField": "VisitingTime", "orderType": 1,
        }
        data = client.post(APPOINTMENTS_PATH, payload).get("data") or {}
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise HikError("A consulta de reservas não retornou lista válida.")
        rows = data["list"]
        for entry in rows:
            if _same_visit(entry, record):
                matches.append(entry)
        total = int(data.get("total") or 0)
        if len(rows) < 100 or (total and page * 100 >= total):
            break
    else:
        raise HikError("Há reservas demais para validar a edição com segurança.")
    if len(matches) != 1:
        raise HikError(
            "Não foi possível associar a visita a uma reserva única no HikCentral. "
            "Edição bloqueada sem alterar o visitante.")
    return matches[0]


def resolve_remote_edit_record(client, record):
    """Apenas leitura: prova registro, grupo, reserva, nível e catracas atuais."""
    from core import verify_visitor_access_plan
    from hikcentral import normalize
    if not record.get("registration_record_id"):
        raise HikError("O HikCentral não retornou o ID do check-in.")
    original_id = client.find_existing_registration(
        record["visitor_id"], record["entry_at"], record["exit_at"])
    if not original_id or original_id != record["registration_record_id"]:
        raise HikError("O check-in original mudou ou não é único; edição bloqueada.")
    appointment = find_original_appointment(client, record)
    levels = parse_access_levels(appointment)
    if len(levels) != 1:
        raise HikError("A reserva não informou exatamente um nível Visitor. Edição bloqueada.")
    requested = [levels[0]["name"]]
    selected, doors = client.resolve_levels(requested)
    verify_visitor_access_plan(selected, doors, requested)
    if str(selected[0]["id"]) != str(levels[0]["id"]):
        raise HikError("O ID do nível retornado pela reserva não corresponde ao nível atual.")
    if record.get("access_names") and record["access_names"] != requested:
        raise HikError("Nível anterior diverge da consulta atual; edição bloqueada.")
    if record.get("door_ids") and set(record["door_ids"]) != set(doors):
        raise HikError("Pontos originais divergem da consulta atual; edição bloqueada.")
    if normalize(str(record.get("group") or "")) != normalize(str(
            (appointment.get("visitorInfo") or {}).get("visitorGroupName") or record.get("group") or "")):
        raise HikError("Grupo da reserva diverge do check-in; edição bloqueada.")
    data = visitor_details(client, record["visitor_id"])
    details_id = str(data.get("visitorId") or data.get("VisitorInfo", {}).get("visitorId") or "")
    if details_id and details_id != record["visitor_id"]:
        raise HikError("Detalhe da pessoa não corresponde ao Visitor ID escolhido.")
    result = dict(record)
    result.update(access_names=requested, door_ids=doors,
                  access_level_ids=[levels[0]["id"]],
                  _remote_verified=True)
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


def fetch_visitor_photo(client, visitor_id: str, pic_url: str = ""):
    if not visitor_id or len(visitor_id) > 64:
        raise ValueError("Visitor ID inválido.")
    # O endereço opaco da foto é encaminhado somente à OpenAPI, nunca aberto
    # como URL externo (evita SSRF e vazamento das credenciais Artemis).
    pic_url = (pic_url or "").strip()
    if len(pic_url) > 256:
        raise HikError("Referência de foto inválida.")
    request = {"visitorId": visitor_id, "picType": "0"}
    if pic_url:
        request["picUrl"] = pic_url
    response = client.post(PHOTO_PATH, request, allow_image_data=True)
    return decode_photo_response(response.get("data"))
