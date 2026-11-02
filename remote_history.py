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
        "checkin_state": STATUSES.get(str(row.get("visitorStatus")), "não informado"),
        "state": "consultar na catraca",
        "photo_url": str(base.get("smallPhotoUrl") or ""),
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
    if not isinstance(data, dict) or not isinstance(data.get("list"), list):
        raise HikError("HikCentral não retornou lista paginada de check-ins.")
    total = int(data.get("totalNum") or 0)
    if total < 0:
        raise HikError("Total inválido no resultado do HikCentral.")
    rows = []
    seen = set()
    for item in data["list"]:
        record = normalize_checkin(item)
        if record and record["visitor_id"] not in seen:
            rows.append(record)
            seen.add(record["visitor_id"])
    return {"rows": rows, "total": total, "page": page, "page_size": page_size,
            "has_next": page < MAX_PAGES and (page*page_size < total if total else len(data["list"]) == page_size)}


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
        binary = base64.b64decode(b64, validate=True)
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
