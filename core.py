"""Regras puras do cadastro local de visitantes; sem dependência de interface."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo
import base64
import json
import os
import re

TZ = ZoneInfo("America/Sao_Paulo")
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
        "host": "192.168.100.102", "port": 37443, "ca_file": "",
        "tickets": json.loads(json.dumps(DEFAULT_TICKETS)),
        "group_access": {group: ACCESS_LEVELS.copy() for group in GROUPS},
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
    hour, minute = map(int, ticket["checkout"].split(":"))
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


def validate_visitor(v: Visitor) -> None:
    if len(v.first_name.strip()) < 2 or len(v.last_name.strip()) < 2:
        raise ValueError("Informe nome e sobrenome.")
    if not v.group.strip():
        raise ValueError("Escolha um grupo de visitantes.")
    if not v.photo_jpeg.startswith(b"\xff\xd8"):
        raise ValueError("Selecione uma foto JPEG válida.")
    if len(v.photo_jpeg) > 2_000_000:
        raise ValueError("Foto maior que 2 MB; comprima a imagem.")
    if not re.fullmatch(r"[A-Za-z0-9]{1,32}", v.card_number):
        raise ValueError("Número da pulseira deve ter 1 a 32 caracteres alfanuméricos.")
    if v.arrival.tzinfo is None or v.departure.tzinfo is None:
        raise ValueError("Datas devem conter fuso horário.")
    if v.departure <= v.arrival:
        raise ValueError("Saída deve ser posterior à entrada.")
    if len(v.reservation) > 255:
        raise ValueError("Número da reserva muito longo.")


def registration_payload(v: Visitor) -> dict:
    validate_visitor(v)
    return {
        "visitStartTime": v.arrival.isoformat(timespec="seconds"),
        "visitEndTime": v.departure.isoformat(timespec="seconds"),
        "visitPurposeType": 4,
        "visitPurpose": "Hospedagem / Day use",
        "visitorInfoList": [{
            "VisitorInfo": {
                "visitorGivenName": v.first_name.strip(),
                "visitorFamilyName": v.last_name.strip(),
                "visitorGroupName": v.group.strip(),
                "remark": v.reservation.strip(),
                "faces": [{"faceData": base64.b64encode(v.photo_jpeg).decode("ascii")}],
                "cards": [{"cardNo": v.card_number.strip()}]
            }
        }]
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
        # Validation using a fixed morning arrival.
        end_for_ticket(datetime(2026, 1, 1, 8, tzinfo=TZ), ticket)
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
        if not group or not values:
            raise ValueError("Todo grupo deve ter ao menos um nível de acesso.")
        mapping[group] = values
    if not mapping:
        raise ValueError("Defina níveis de acesso.")
    return mapping


def save_pending(visitor_id: str, access_ids: list[str], base: Path | None = None) -> None:
    """Grava só IDs necessários para retomar; sem foto, nome, reserva ou segredos."""
    path = (base or app_dir()) / "pending.json"
    if path.exists():
        raise RuntimeError("Há cadastro pendente. Conclua-o antes de criar outro.")
    path.write_text(json.dumps({"visitor_id": visitor_id,
                               "access_ids": access_ids, "assigned": []}), encoding="utf-8")


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
    path.write_text(json.dumps(data), encoding="utf-8")


def clear_pending(base: Path | None = None) -> None:
    ((base or app_dir()) / "pending.json").unlink(missing_ok=True)
