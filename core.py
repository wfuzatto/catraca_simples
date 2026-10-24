"""Regras puras do cadastro local de visitantes; sem dependência de interface."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
import base64
import json
import os
import re
from PIL import Image, ImageOps

TZ = ZoneInfo("America/Sao_Paulo")
APP_VERSION = "1.0.0-preview.2"
GROUPS = ["Day use", "Hóspedes Serra", "Hóspedes Vale", "Visitors"]
ACCESS_LEVELS = ["CATRACAS SABIA", "ENTRADA ACQUAVALE", "LOJA ACQUAVALE", "SAIDA ACQUAVALE"]
DEFAULT_TICKETS = {"1 dia": {"days": 1, "checkout": "23:59"},
                   "2 dias": {"days": 2, "checkout": "23:59"},
                   "3 dias": {"days": 3, "checkout": "23:59"}}


def app_dir() -> Path:
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ValeCatracaSimples"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_settings() -> dict:
    return {
        "host": "127.0.0.1", "port": 443, "ca_file": "",
        "cert_pin_sha256": "",
        "tickets": json.loads(json.dumps(DEFAULT_TICKETS)),
        # A recepção must assign only levels approved for each group.
        "group_access": {group: [] for group in GROUPS},
        "card_format": "DECIMAL_BIG", "camera_index": 0
    }


def load_settings(base: Path | None = None) -> dict:
    conf = default_settings()
    path = (base or app_dir()) / "settings.json"
    if path.is_file():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("Configuração inválida.")
        for key in conf:
            if key in saved:
                conf[key] = saved[key]
        saved_access = conf.get("group_access")
        if (isinstance(saved_access, dict)
                and set(saved_access) == set(GROUPS)
                and all(isinstance(saved_access[group], list)
                        and set(saved_access[group]) == set(ACCESS_LEVELS)
                        for group in GROUPS)):
            # Older builds granted every configured level to every group by default.
            # Require the operator to choose the approved mapping before new writes.
            conf["group_access"] = {group: [] for group in GROUPS}
    return conf


def save_settings(settings: dict, base: Path | None = None) -> None:
    dest = base or app_dir()
    dest.mkdir(parents=True, exist_ok=True)
    safe = {k: settings[k] for k in default_settings() if k in settings}
    temp = dest / "settings.tmp"
    temp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, dest / "settings.json")


def save_api_keys(app_key: str, app_secret: str, base: Path | None = None) -> None:
    """Segredos vinculados ao usuário Windows via DPAPI; falhar fechado fora do Windows."""
    if os.name != "nt":
        raise RuntimeError("Armazenamento DPAPI requer Windows.")
    import win32crypt
    dest = base or app_dir()
    dest.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"app_key": app_key, "app_secret": app_secret}).encode("utf-8")
    encrypted = win32crypt.CryptProtectData(payload, "ValeCatraca", None, None, None, 0)
    path = dest / "secrets.dpapi"
    path.write_bytes(encrypted)


def load_api_keys(base: Path | None = None) -> tuple[str, str]:
    path = (base or app_dir()) / "secrets.dpapi"
    if not path.exists():
        return "", ""
    if os.name != "nt":
        raise RuntimeError("Chaves DPAPI só podem ser lidas no Windows do usuário.")
    import win32crypt
    raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 0)[1]
    data = json.loads(raw.decode("utf-8"))
    return data.get("app_key", ""), data.get("app_secret", "")


def parse_local(value: str) -> datetime:
    dt = datetime.strptime(value.strip(), "%d/%m/%Y %H:%M")
    return dt.replace(tzinfo=TZ)


def end_for_ticket(start: datetime, ticket: dict) -> datetime:
    days = int(ticket["days"])
    if not (1 <= days <= 365):
        raise ValueError("Duração do ingresso deve ser de 1 a 365 dias.")
    checkout = str(ticket["checkout"])
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", checkout):
        raise ValueError("Horário de vencimento deve usar HH:MM.")
    hour, minute = map(int, checkout.split(":"))
    end_date = start.date() + timedelta(days=days - 1)
    end = datetime.combine(end_date, time(hour, minute, 59), tzinfo=TZ)
    if end <= start:
        raise ValueError("Saída calculada não é posterior à entrada; ajuste o horário.")
    return end


@dataclass
class Visitor:
    first_name: str
    last_name: str
    group: str
    photo_jpeg: bytes
    card_number: str
    arrival: datetime
    departure: datetime
    reservation: str = ""


def prepare_face_jpeg(image_bytes: bytes, max_bytes: int = 2_000_000) -> bytes:
    """Normalize a selected/captured image to an oriented, compact JPEG."""
    if not image_bytes or len(image_bytes) > 25_000_000:
        raise ValueError("A imagem está vazia ou excede 25 MB.")
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            if source.format not in {"JPEG", "PNG", "BMP"}:
                raise ValueError("Use uma imagem JPEG, PNG ou BMP.")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Não foi possível ler a imagem selecionada.") from exc
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    for quality in (88, 82, 76, 70, 64, 58, 52):
        output = BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=True)
        data = output.getvalue()
        if len(data) <= max_bytes:
            return data
    raise ValueError("A foto não pôde ser reduzida para menos de 2 MB.")


def validate_visitor(v: Visitor, now: datetime | None = None) -> None:
    if len(v.first_name.strip()) < 2 or len(v.last_name.strip()) < 2:
        raise ValueError("Informe nome e sobrenome.")
    if not v.group.strip():
        raise ValueError("Escolha um grupo de visitantes.")
    card_number = v.card_number.strip()
    if not v.photo_jpeg and not card_number:
        raise ValueError("Informe reconhecimento facial, pulseira/cart\u00e3o ou ambos.")
    if v.photo_jpeg:
        try:
            with Image.open(BytesIO(v.photo_jpeg)) as image:
                if image.format != "JPEG":
                    raise ValueError
                image.verify()
        except Exception as exc:
            raise ValueError("Selecione uma foto JPEG v\u00e1lida.") from exc
        if len(v.photo_jpeg) > 2_000_000:
            raise ValueError("Foto maior que 2 MB; comprima a imagem.")
    if card_number and not re.fullmatch(r"[A-Za-z0-9]{1,32}", card_number):
        raise ValueError("N\u00famero da pulseira deve ter 1 a 32 caracteres alfanum\u00e9ricos.")
    if (v.arrival.tzinfo is None or v.departure.tzinfo is None
            or v.arrival.utcoffset() is None or v.departure.utcoffset() is None):
        raise ValueError("Datas devem conter fuso hor\u00e1rio.")
    if v.departure <= v.arrival:
        raise ValueError("Sa\u00edda deve ser posterior \u00e0 entrada.")
    current = now or datetime.now(TZ)
    if v.departure.astimezone(TZ) <= current.astimezone(TZ):
        raise ValueError("O ingresso j\u00e1 expirou. Ajuste a data e hora de sa\u00edda.")
    if len(v.reservation) > 255:
        raise ValueError("N\u00famero da reserva muito longo.")


def registration_payload(v: Visitor) -> dict:
    validate_visitor(v)
    visitor_info = {
        "visitorGivenName": v.first_name.strip(),
        "visitorFamilyName": v.last_name.strip(),
        "visitorGroupName": v.group.strip(),
        "remark": v.reservation.strip(),
        "gender": 0,
        "companyName": "Vale",
        "certificateType": 111,
        "certificateNo": "",
        "accessInfo": {"electrostaticDetectionType": 0, "qrCodeValidNum": 1},
    }
    if v.photo_jpeg:
        visitor_info["faces"] = [{"faceData": base64.b64encode(v.photo_jpeg).decode("ascii")}]
    if v.card_number.strip():
        visitor_info["cards"] = [{"cardNo": v.card_number.strip()}]
    return {
        "visitStartTime": v.arrival.isoformat(timespec="seconds"),
        "visitEndTime": v.departure.isoformat(timespec="seconds"),
        "visitPurposeType": 4,
        "visitPurpose": "Hospedagem / Day use",
        "visitorInfoList": [{"VisitorInfo": visitor_info}]
    }


def parse_ticket_lines(raw: str) -> dict:
    tickets = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        fields = [p.strip() for p in line.split(";")]
        if len(fields) != 3 or not fields[0]:
            raise ValueError("Ingressos: use Nome;Dias;HH:MM por linha.")
        ticket = {"days": int(fields[1]), "checkout": fields[2]}
        if fields[0] in tickets:
            raise ValueError(f"Tipo de ingresso duplicado: {fields[0]}.")
        # Validate time and duration independent of a guest's arrival hour.
        end_for_ticket(datetime(2026, 1, 1, 0, tzinfo=TZ), ticket)
        tickets[fields[0]] = ticket
    if not tickets:
        raise ValueError("Cadastre pelo menos um ingresso.")
    return tickets


def parse_access_lines(raw: str) -> dict:
    mapping = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        if "=" not in line:
            raise ValueError("Níveis: use Grupo=Nivel1,Nivel2 por linha.")
        group, levels = line.split("=", 1)
        group = group.strip()
        values = list(dict.fromkeys(x.strip() for x in levels.split(",") if x.strip()))
        if not group:
            raise ValueError("Informe o nome do grupo antes do sinal =.")
        mapping[group] = values
    if not mapping:
        return {group: [] for group in GROUPS}
    return mapping


def _write_json_atomic(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def save_pending(visitor_id: str, access_ids: list[str], base: Path | None = None,
                 *, appointment_id: str = "", door_ids: list[str] | None = None,
                 registered: bool = True, expect_face: bool = True,
                 expect_card: bool = True) -> None:
    """Checkpoint mínimo após reserva; nunca armazena foto, cartão, segredo ou nome."""
    path = (base or app_dir()) / "pending.json"
    if not expect_face and not expect_card:
        raise ValueError("Informe reconhecimento facial, pulseira/cart\u00e3o ou ambos.")
    if path.exists():
        raise RuntimeError("Há cadastro pendente. Confira o cadastro anterior antes de criar outro.")
    _write_json_atomic(path, {
        "visitor_id": visitor_id, "appointment_id": appointment_id,
        "access_ids": access_ids, "door_ids": door_ids or [],
        "expect_face": bool(expect_face), "expect_card": bool(expect_card),
        "registration_record_id": "", "registered": registered, "assigned": []
    })


def mark_registered(registration_record_id: str = "", base: Path | None = None) -> None:
    path = (base or app_dir()) / "pending.json"
    data = load_pending(base)
    if not data:
        raise RuntimeError("Não há cadastro pendente.")
    data["registered"] = True
    data["registration_record_id"] = registration_record_id
    _write_json_atomic(path, data)


def load_pending(base: Path | None = None) -> dict | None:
    path = (base or app_dir()) / "pending.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def mark_assigned(group_id: str, base: Path | None = None) -> None:
    path = (base or app_dir()) / "pending.json"
    data = load_pending(base)
    if not data:
        raise RuntimeError("Nenhum cadastro pendente.")
    if group_id not in data["assigned"]:
        data["assigned"].append(group_id)
    _write_json_atomic(path, data)


def clear_pending(base: Path | None = None) -> None:
    ((base or app_dir()) / "pending.json").unlink(missing_ok=True)


def save_last_delivery(visitor_id: str, door_ids: list[str], base: Path | None = None,
                       *, expect_face: bool = True, expect_card: bool = True) -> None:
    """Store only IDs and requested credential types for rechecking delivery."""
    if not visitor_id or not door_ids:
        raise ValueError("Visitante e catracas s\u00e3o obrigat\u00f3rios para consultar a entrega.")
    if not expect_face and not expect_card:
        raise ValueError("Informe reconhecimento facial, pulseira/cart\u00e3o ou ambos.")
    dest = base or app_dir()
    dest.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(dest / "last_delivery.json", {
        "visitor_id": str(visitor_id),
        "door_ids": list(dict.fromkeys(str(x) for x in door_ids)),
        "expect_face": bool(expect_face), "expect_card": bool(expect_card)
    })


def load_last_delivery(base: Path | None = None) -> dict | None:
    path = (base or app_dir()) / "last_delivery.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
