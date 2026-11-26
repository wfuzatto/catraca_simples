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
import shutil
from PIL import Image, ImageOps

TZ = ZoneInfo("America/Sao_Paulo")
APP_VERSION = "1.0.0-preview.85"
GROUPS = ["Day use", "Hóspedes Serra", "Hóspedes Vale", "Visitors"]
ACCESS_LEVELS = ["CATRACAS SABIA", "ENTRADA ACQUAVALE", "LOJA ACQUAVALE", "SAIDA ACQUAVALE"]
# Configuração final dos níveis Visitor informada e autorizada pelo hotel.
# NÃO incluir níveis All-Day/funcionários, mesmo que apareçam na UI Access Control.
VALE_VISITOR_LEVELS = {
    "Day use": "DAY_USE",
    "Hóspedes Serra": "VISITANTE_SERRA",
    "Hóspedes Vale": "VISITANTE_VALE",
}
VALE_MAPPING_REVISION = 1
# Não limitar reutilizações via qrCodeValidNum em novos visitantes.
VALE_VALID_TIMES = 0

DEFAULT_TICKETS = {
    f"{days} dia" if days == 1 else f"{days} dias": {"days": days}
    for days in range(1, 10)
}


def app_dir() -> Path:
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ValeCatracaSimples"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_settings() -> dict:
    return {
        "host": "127.0.0.1", "port": 443, "ca_file": "",
        "cert_pin_sha256": "",
        "preferred_group": "",  # Sugestão local, nunca substitui consulta da OpenAPI.
        "auto_checkin": False,
        "valid_times": VALE_VALID_TIMES,  # Travado em zero para não limitar a utilização.
        "tickets": json.loads(json.dumps(DEFAULT_TICKETS)),
        "group_access": {
            group: [VALE_VISITOR_LEVELS[group]] if group in VALE_VISITOR_LEVELS else []
            for group in GROUPS
        },
        "access_mapping_revision": VALE_MAPPING_REVISION,
        "card_format": "DECIMAL_LITTLE", "camera_index": 0,
        # Captura facial inteligente; perfis antigos recebem defaults via load_settings.
        "face_auto_capture": True,
        "face_stability_ms": 900,
        "face_sensitivity": "Normal",
        "face_require_quality": True,
        "face_timeout_s": 35
    }


def validate_visitor_group_selection(
    selected: str, available: list[str], preferred: str = ""
) -> str:
    """Falha fechada se houver preferência obrigatória inválida ou burlada."""
    if preferred and preferred not in available:
        raise ValueError(
            f"Grupo preferencial '{preferred}' não existe mais no HikCentral. "
            "Altere o grupo em Configurações antes de cadastrar."
        )
    if preferred and selected != preferred:
        raise ValueError(
            "Grupo bloqueado neste ponto de venda. "
            f"Somente '{preferred}' está autorizado pela configuração local."
        )
    if selected not in available:
        raise ValueError("Grupo não confirmado pelos grupos reais do HikCentral.")
    return selected


def visitor_levels_for_group(
    group: str, available_groups: list[str], mapping: dict[str, list[str]],
    preferred: str = ""
) -> list[str]:
    """Each Visitor group has one explicitly approved Visitor access level.

    A Visitor group is not an employee department. Only levels returned by the
    OpenAPI with type=2 may be resolved and granted.
    """
    validate_visitor_group_selection(group, available_groups, preferred)
    assigned = mapping.get(group)
    if not isinstance(assigned, list) or len(assigned) != 1:
        raise ValueError(
            f"Configure exatamente um nível Visitor para o grupo '{group}' "
            "em Configurações > Níveis de acesso por grupo. "
            "Nenhum cadastro foi enviado."
        )
    name = assigned[0]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Nível Visitor configurado é inválido.")
    return [name.strip()]


def verify_visitor_access_plan(
    selected: list[dict], doors: list[str], requested: list[str]
) -> None:
    """Check one configured type=2 level and use all its returned doors."""
    if len(requested) != 1 or len(selected) != 1:
        raise ValueError("Cada grupo deve ter exatamente um nível Visitor.")
    level = selected[0]
    name, level_id = str(level.get("name", "")).strip(), str(level.get("id", "")).strip()
    if not level_id or not name or not isinstance(doors, list):
        raise ValueError("Nível Visitor/IDs dos pontos de acesso inválidos.")
    if not doors or any(not str(door).strip() for door in doors) or len(set(doors)) != len(doors):
        raise ValueError("O nível Visitor não possui uma lista válida de pontos de acesso.")
    # The HikCentral level profile is the source of truth for its access points.
    # Do not impose a local point-count policy or omit doors from that profile.



def initial_visitor_group(available: list[str], preferred: str = "") -> str:
    """Sugestão inicial; só seleciona um grupo retornado pela consulta real."""
    if preferred and preferred in available:
        return preferred
    return available[0] if available else ""



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
        # Migrate legacy ticket entries to duration-only options. Daily access
        # hours belong to the HikCentral gate profile, not this application.
        saved_tickets = conf.get("tickets")
        if not isinstance(saved_tickets, dict):
            raise ValueError("Ingressos salvos inválidos.")
        tickets = json.loads(json.dumps(DEFAULT_TICKETS))
        for name, ticket in saved_tickets.items():
            if not isinstance(ticket, dict):
                raise ValueError("Ingresso salvo inválido.")
            try:
                days = int(ticket.get("days"))
            except (TypeError, ValueError):
                continue
            if str(name).strip() and 1 <= days <= 9:
                tickets[str(name).strip()] = {"days": days}
        conf["tickets"] = tickets
        saved_access = conf.get("group_access")
        if not isinstance(saved_access, dict):
            saved_access = {}
        # Migração única das configurações locais anteriores à organização final:
        # Day use / Serra / Vale recebem seus níveis Visitor type=2 aprovados.
        # Outros grupos não recebem permissões automaticamente.
        if int(saved.get("access_mapping_revision", 0)) < VALE_MAPPING_REVISION:
            updated = dict(saved_access)
            legacy_all = (
                set(saved_access) == set(GROUPS)
                and all(isinstance(saved_access.get(group), list)
                        and set(saved_access[group]) == set(ACCESS_LEVELS)
                        for group in GROUPS)
            )
            if legacy_all:
                updated = {group: [] for group in GROUPS}
            else:
                # Remover concessões antigas idênticas ao conjunto All-Day,
                # sem alterar níveis customizados de outros grupos.
                for group, levels in list(updated.items()):
                    if isinstance(levels, list) and set(levels) == set(ACCESS_LEVELS):
                        updated[group] = []
            for group, level in VALE_VISITOR_LEVELS.items():
                updated[group] = [level]
            conf["group_access"] = updated
            conf["access_mapping_revision"] = VALE_MAPPING_REVISION
        # Um operador podia salvar 1..100 nas versões anteriores.
        # Corrigir APENAS a chave de VALID TIMES, mantendo todas as demais
        # preferências, e preservar cópia do arquivo anterior uma única vez.
        # Faz a migração também ao reabrir o EXE, sem depender de Salvar.
        if saved.get("valid_times", VALE_VALID_TIMES) != VALE_VALID_TIMES:
            backup = path.with_name("settings.backup-pre-valid-times-zero.json")
            if not backup.exists():
                shutil.copy2(path, backup)
            migrated = dict(saved)
            migrated["valid_times"] = VALE_VALID_TIMES
            temp = path.with_name("settings.valid-times.tmp")
            try:
                temp.write_text(
                    json.dumps(migrated, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)
        # Arquivos atualizados podem ser personalizados livremente sem
        # remapeamento automático em reinicializações futuras.
    conf["valid_times"] = VALE_VALID_TIMES
    return conf


def save_settings(settings: dict, base: Path | None = None) -> None:
    dest = base or app_dir()
    dest.mkdir(parents=True, exist_ok=True)
    safe = {k: settings[k] for k in default_settings() if k in settings}
    # Não permitir que configuracões antigas/telas desatualizadas gravem 1..100.
    safe["valid_times"] = VALE_VALID_TIMES
    original = dest / "settings.json"
    backup = dest / "settings.backup-pre-visitor-levels.json"
    # Preserve a recoverable copy of the old local group mappings on first save.
    if original.is_file() and not backup.exists():
        try:
            old_settings = json.loads(original.read_text(encoding="utf-8"))
            if isinstance(old_settings, dict) and int(
                old_settings.get("access_mapping_revision", 0)
            ) < VALE_MAPPING_REVISION:
                shutil.copy2(original, backup)
        except (json.JSONDecodeError, ValueError, TypeError):
            # Malformed legacy config must not be erased by automatic migration.
            raise ValueError("Configuração anterior inválida; backup manual necessário.")
    temp = dest / "settings.tmp"
    temp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, original)


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


def visitor_validity_hours(settings: dict | None = None) -> tuple[time, time]:
    """Visitor records always span the full calendar day; gate profiles limit use."""
    return time(0, 0, 0), time(23, 59, 59)


def parse_visit_date(value: str):
    """Recebe somente DD/MM/AAAA; nunca aceita horas no campo do caixa."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value.strip()):
        raise ValueError("Entrada e Saída devem conter apenas a data DD/MM/AAAA.")
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date()
    except ValueError as exc:
        raise ValueError("Data inválida. Informe DD/MM/AAAA.") from exc


def visitor_period_from_dates(entry_date: str, exit_date: str, settings: dict,
                              ticket: dict | None = None) -> tuple[datetime, datetime]:
    """Constrói a validade Visitor de dia inteiro nas datas escolhidas.

    O perfil da catraca no HikCentral controla os horários diários de passagem.
    """
    entry = parse_visit_date(entry_date)
    exit_ = parse_visit_date(exit_date)
    if exit_ < entry:
        raise ValueError("Data de saída anterior à data de entrada.")
    days = (exit_ - entry).days + 1
    if not 1 <= days <= 9:
        raise ValueError("Período de visitante deve ter entre 1 e 9 dias.")
    if ticket is not None:
        try:
            approved_days = int(ticket["days"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Ingresso sem duração válida.") from exc
        if not 1 <= approved_days <= 9:
            raise ValueError("Ingresso deve ter entre 1 e 9 dias.")
        if days != approved_days:
            raise ValueError(
                f"Ingresso de {approved_days} dia(s): a saída deve ser "
                f"{(entry + timedelta(days=approved_days - 1)):%d/%m/%Y}.")
    start_time, end_time = visitor_validity_hours()
    start = datetime.combine(entry, start_time, tzinfo=TZ)
    end = datetime.combine(exit_, end_time, tzinfo=TZ)
    if end <= start:
        raise ValueError("Período inválido: término não pode anteceder o início.")
    return start, end


def parse_local(value: str) -> datetime:
    dt = datetime.strptime(value.strip(), "%d/%m/%Y %H:%M")
    return dt.replace(tzinfo=TZ)


def end_for_ticket(start: datetime, ticket: dict) -> datetime:
    """Returns the selected ticket's last date at 23:59:59 local time."""
    days = int(ticket["days"])
    if not 1 <= days <= 9:
        raise ValueError("Duração do ingresso deve ser de 1 a 9 dias.")
    end_date = start.astimezone(TZ).date() + timedelta(days=days - 1)
    end = datetime.combine(end_date, time(23, 59, 59), tzinfo=TZ)
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

def face_cover_preview(image_bytes: bytes, width: int = 148, height: int = 133) -> Image.Image:
    """Cover preview: fill the frame by proportional resizing and central crop."""
    if width < 1 or height < 1:
        raise ValueError("Dimensões da foto inválidas.")
    with Image.open(BytesIO(image_bytes)) as original:
        face = ImageOps.exif_transpose(original).convert("RGB")
        return ImageOps.fit(face, (width, height), method=Image.Resampling.LANCZOS,
                            centering=(0.5, 0.5))




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


def validate_visitor(v: Visitor, now: datetime | None = None, *, allow_expired=False,
                     require_credential=True) -> None:
    if len(v.first_name.strip()) < 2 or len(v.last_name.strip()) < 2:
        raise ValueError("Informe nome e sobrenome.")
    if not v.group.strip():
        raise ValueError("Escolha um grupo de visitantes.")
    card_number = v.card_number.strip()
    if require_credential and not v.photo_jpeg and not card_number:
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
    if not allow_expired and v.departure.astimezone(TZ) <= current.astimezone(TZ):
        raise ValueError("O ingresso j\u00e1 expirou. Ajuste a data e hora de sa\u00edda.")
    if len(v.reservation) > 255:
        raise ValueError("N\u00famero da reserva muito longo.")


def validate_valid_times(value: int) -> int:
    """HikCentral VisitorInfo.accessInfo.qrCodeValidNum: 0 ou 1..100.

    Conforme OpenAPI 2.6.1/3.0.1, o campo refere-se a autenticação por QR.
    O efeito em reconhecimento facial/NFC requer verificação no dispositivo.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("VALID TIMES deve ser um inteiro entre 0 e 100 (0 = desabilitado).")
    return value


def registration_payload(v: Visitor, valid_times: int = 0, *, editing=False,
                         require_credential=True) -> dict:
    validate_visitor(v, allow_expired=editing, require_credential=require_credential)
    validate_valid_times(valid_times)  # Contrato legado; nenhuma limitação é aplicada.
    valid_times = VALE_VALID_TIMES  # Regra obrigatória independentemente do perfil local.
    visitor_info = {
        "visitorGivenName": v.first_name.strip(),
        "visitorFamilyName": v.last_name.strip(),
        "visitorGroupName": v.group.strip(),
        "remark": v.reservation.strip(),
        "gender": 0,
        "companyName": "Vale",
        "certificateType": 111,
        "certificateNo": "",
        "accessInfo": {"electrostaticDetectionType": 0, "qrCodeValidNum": valid_times},
    }
    if v.photo_jpeg:
        visitor_info["faces"] = [{"faceData": base64.b64encode(v.photo_jpeg).decode("ascii")}]
    if v.card_number.strip():
        visitor_info["cards"] = [{"cardNo": v.card_number.strip()}]
    return {
        "visitStartTime": v.arrival.astimezone(TZ).isoformat(timespec="seconds"),
        "visitEndTime": v.departure.astimezone(TZ).isoformat(timespec="seconds"),
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
        if len(fields) not in (2, 3, 4) or not fields[0]:
            raise ValueError("Ingressos: use Nome;Dias (de 1 a 9) por linha.")
        try:
            days = int(fields[1])
        except ValueError as exc:
            raise ValueError("Ingresso deve ter de 1 a 9 dias.") from exc
        if not 1 <= days <= 9:
            raise ValueError("Ingresso deve ter de 1 a 9 dias.")
        if fields[0] in tickets:
            raise ValueError(f"Tipo de ingresso duplicado: {fields[0]}.")
        # Extra legacy columns are ignored; only HikCentral profiles set hours.
        tickets[fields[0]] = {"days": days}
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



def mark_appointment_inflight(base: Path | None = None, *, encrypted_context: str = "") -> None:
    """Persist a non-personal checkpoint BEFORE the first remote write.

    If the response to appointment is lost, do not automatically resubmit.
    """
    path = (base or app_dir()) / "pending.json"
    if path.exists():
        raise RuntimeError("Há cadastro pendente; concilie antes de nova reserva.")
    _write_json_atomic(path, {
        "stage": "appointment_inflight",
        "visitor_id": "", "appointment_id": "",
        "access_ids": [], "door_ids": [], "registered": False,
        "assigned": [], "expect_face": False, "expect_card": False,
        "encrypted_context": encrypted_context,
    })



def save_pending(visitor_id: str, access_ids: list[str], base: Path | None = None,
                 *, appointment_id: str = "", door_ids: list[str] | None = None,
                 registered: bool = True, expect_face: bool = True,
                 expect_card: bool = True, entry_at: str = "", exit_at: str = "",
                 access_names: list[str] | None = None,
                 checkin_requested: bool = True, card_confirmed: bool | None = None,
                 direct_checkin_nfc: bool = False) -> None:
    """Checkpoint mínimo após reserva; nunca armazena foto, cartão, segredo ou nome."""
    path = (base or app_dir()) / "pending.json"
    if not expect_face and not expect_card:
        raise ValueError("Informe reconhecimento facial, pulseira/cart\u00e3o ou ambos.")
    old = load_pending(base) if path.exists() else None
    if path.exists():
        if not (isinstance(old, dict) and old.get("stage") == "appointment_inflight"):
            raise RuntimeError("Há cadastro pendente. Confira o cadastro anterior antes de criar outro.")
    data = {
        "stage": "appointment_confirmed",
        "visitor_id": visitor_id, "appointment_id": appointment_id,
        "access_ids": access_ids, "door_ids": door_ids or [],
        "access_names": access_names or [],
        "expect_face": bool(expect_face), "expect_card": bool(expect_card),
        "registration_record_id": "", "registered": registered,
        "checkin_requested": bool(checkin_requested), "checkin_inflight": False,
        "direct_checkin_nfc": bool(direct_checkin_nfc),
        "assigned": [],
        "entry_at": entry_at, "exit_at": exit_at
    }
    if card_confirmed is not None:
        data["card_confirmed"] = card_confirmed
    # O contexto permanece cifrado com DPAPI ao longo de todos os checkpoints.
    # Nunca copiá-lo em histórico, logs ou mensagens de erro.
    if isinstance(old, dict) and old.get("encrypted_context"):
        data["encrypted_context"] = old["encrypted_context"]
    _write_json_atomic(path, data)


def mark_checkin_started(base: Path | None = None) -> None:
    """Checkpoint ANTES do POST; depois dele, nunca repetir um check-in incerto."""
    data = load_pending(base)
    if not data or data.get("stage") != "appointment_confirmed":
        raise RuntimeError("Não há reserva confirmada para iniciar o check-in.")
    if data.get("registered") or data.get("checkin_inflight"):
        raise RuntimeError("Check-in já confirmado ou com resultado incerto; não reenviar.")
    data["checkin_inflight"] = True
    _write_json_atomic((base or app_dir()) / "pending.json", data)


def mark_card_confirmed(visitor_id: str, base: Path | None = None) -> None:
    data = load_pending(base)
    if not data or data.get("visitor_id") != visitor_id:
        raise RuntimeError("A pendência da pulseira pertence a outro cadastro.")
    data["card_confirmed"] = True
    _write_json_atomic((base or app_dir()) / "pending.json", data)


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
                       *, expect_face: bool = True, expect_card: bool = True,
                       entry_at: str = "", exit_at: str = "",
                       appointment_id: str = "", registration_record_id: str = "") -> None:
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
        "expect_face": bool(expect_face), "expect_card": bool(expect_card),
        "entry_at": entry_at, "exit_at": exit_at,
        "appointment_id": str(appointment_id),
        "registration_record_id": str(registration_record_id)
    })


def load_last_delivery(base: Path | None = None) -> dict | None:
    path = (base or app_dir()) / "last_delivery.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
