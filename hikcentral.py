"""HikCentral OpenAPI - adaptado do Vale Visitor funcional (hcp.php)."""
from __future__ import annotations
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import time
import unicodedata
from urllib.parse import urlparse
import requests


class HikError(RuntimeError):
    pass


def normalize(name: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFD", name)
                            if unicodedata.category(c) != "Mn").casefold().split())


def sign(path: str, key: str, secret: str, timestamp: str,
         content_md5: str = "", nonce: str = "") -> str:
    # Igual ao Vale Visitor PHP: POST, Accept, Content-MD5, Content-Type,
    # x-ca-key, x-ca-nonce, x-ca-timestamp e path exato da API.
    canonical = (f"POST\n*/*\n{content_md5}\napplication/json\n"
                 f"x-ca-key:{key}\nx-ca-nonce:{nonce}\n"
                 f"x-ca-timestamp:{timestamp}\n{path}")
    digest = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def normalize_pin(value: str) -> str:
    """Exige SHA-256 completo, sem aceitar pins curtos ou parciais."""
    value = value.strip().replace(":", "").replace(" ", "").upper()
    if value and not re.fullmatch(r"[0-9A-F]{64}", value):
        raise HikError("A impressão digital precisa ter 64 caracteres hexadecimais SHA-256.")
    return value


def read_cert_sha256(host: str, port: int, timeout: float = 7.0) -> str:
    """Lê somente o certificado remoto: não envia AppKey, AppSecret nem qualquer PII."""
    if not host or any(ch in host for ch in ("/", "\\", "://")):
        raise HikError("IP/DNS inválido.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
                if not der:
                    raise HikError("O servidor não apresentou certificado TLS.")
                return hashlib.sha256(der).hexdigest().upper()
    except (OSError, ssl.SSLError) as exc:
        raise HikError(f"Não foi possível ler certificado em {host}:{port}: {exc}") from exc


def pinned_https_post(host: str, port: int, path: str, data: bytes,
                      headers: dict, fingerprint: str, timeout: float = 25.0) -> tuple[int, bytes]:
    """Autentica o certificado ANTES de enviar qualquer segredo/dado pessoal.

    TLS transport encryption without CA validation is acceptable only in this
    explicitly fingerprint-pinned mode. Never fall back if fingerprint differs.
    """
    fingerprint = normalize_pin(fingerprint)
    if not fingerprint:
        raise HikError("Impressão digital TLS não configurada.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    conn = http.client.HTTPSConnection(host, int(port), context=context, timeout=timeout)
    try:
        conn.connect()  # Executa TLS handshake. Ainda não transmite cabeçalhos/autenticação.
        cert = conn.sock.getpeercert(binary_form=True) if conn.sock else None
        if not cert:
            raise HikError("O serviço não apresentou certificado TLS.")
        presented = hashlib.sha256(cert).hexdigest().upper()
        if not hmac.compare_digest(presented, fingerprint):
            raise HikError(
                "O CERTIFICADO DO HIKCENTRAL MUDOU ou não corresponde ao SHA-256 confiável. "
                "Nenhuma credencial foi enviada. Confira o certificado no servidor antes de atualizar."
            )
        conn.request("POST", path, body=data, headers=headers)
        response = conn.getresponse()
        content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise HikError("Resposta HTTP muito grande.")
        return response.status, content
    except (TimeoutError, OSError, http.client.HTTPException, ssl.SSLError) as exc:
        raise HikError(f"Falha HTTPS com certificado fixado em {host}:{port}: {exc}") from exc
    finally:
        conn.close()


def network_diagnostics(host: str, port: int, ca_file: str = "",
                        timeout: float = 3.0, pinned_sha256: str = "") -> str:
    """Diagnóstico não autenticado, sem alterar firewall/TLS nem enviar credenciais."""
    host = host.strip()
    if not host or any(x in host for x in ("/", "\\", "://")):
        raise HikError("IP/DNS inválido: informe somente o host, sem https://.")
    lines = [f"Alvo: {host}:{port}"]
    try:
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = list(dict.fromkeys(row[4][0] for row in info))
        lines.append("DNS: " + ", ".join(ips[:5]))
    except OSError as exc:
        raise HikError(f"Falha de resolução de DNS de {host}: {exc}") from exc
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            lines.append(f"TCP {port}: ACESSÍVEL")
            if pinned_sha256:
                expected = normalize_pin(pinned_sha256)
                actual = read_cert_sha256(host, port, timeout)
                if not hmac.compare_digest(actual, expected):
                    lines.append("TLS: certificado inválido ou alterado; impressão SHA-256 não confere.")
                    return "\n".join(lines)
                lines.append("TLS: certificado corresponde à impressão SHA-256 aprovada.")
                lines.append("Pronto para testar autenticação OpenAPI.")
                return "\n".join(lines)
            context = ssl.create_default_context(cafile=ca_file or None)
            try:
                with context.wrap_socket(sock, server_hostname=host) as tls:
                    lines.append(f"TLS: OK ({tls.version()})")
                    lines.append("Pronto para testar autenticação OpenAPI.")
            except ssl.SSLCertVerificationError as exc:
                lines.append(f"TLS: certificado/hostname inválido: {exc}")
                lines.append("Use DNS/IP presente no SAN e CA PEM correta.")
            except ssl.SSLError as exc:
                lines.append(f"TLS: handshake falhou: {exc}")
                lines.append("A porta aceita TCP, mas pode não ser HTTPS/OpenAPI.")
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        lines.append(f"TCP {port}: FALHOU ({type(exc).__name__}: {exc})")
        other = 37443 if port == 443 else 443
        try:
            with socket.create_connection((host, other), timeout=timeout):
                lines.append(f"TCP {other}: ACESSÍVEL. Confira porta OpenAPI.")
        except OSError:
            lines.append(f"TCP {other}: também não acessível.")
        lines.append("Verifique no servidor se o serviço escuta na LAN, firewall e rota/VPN.")
    return "\n".join(lines)


class HikCentral:
    def __init__(self, host: str, port: int, key: str, secret: str,
                 ca_file: str = "", session=None, pinned_sha256: str = ""):
        if not host or not key or not secret:
            raise HikError("Configure host e credenciais OpenAPI (AppKey/AppSecret).")
        if "://" in host or "/" in host or "\\" in host:
            raise HikError("Host inválido; informe apenas IP ou DNS.")
        self.host, self.port = host, int(port)
        self.url = f"https://{host}:{int(port)}"
        self.key, self.secret = key, secret
        self.verify = ca_file if ca_file else True
        self.pinned_sha256 = normalize_pin(pinned_sha256)
        if self.pinned_sha256 and ca_file:
            raise HikError("Selecione apenas CA PEM ou certificado fixado por SHA-256, não ambos.")
        self.session = session or requests.Session()
        # Nunca usar proxy HTTP do usuário para o IP interno do HikCentral.
        if session is None:
            self.session.trust_env = False

    def post(self, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload or {}, ensure_ascii=False,
                          separators=(",", ":")).encode("utf-8")
        md5 = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")
        timestamp = str(int(time.time() * 1000))
        nonce = secrets.token_hex(16)
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Content-MD5": md5,
            "x-ca-key": self.key,
            "x-ca-nonce": nonce,
            "x-ca-timestamp": timestamp,
            "x-ca-signature-headers": "x-ca-key,x-ca-nonce,x-ca-timestamp",
            "x-ca-signature": sign(path, self.key, self.secret, timestamp, md5, nonce),
            "userId": "admin",
        }
        if self.pinned_sha256:
            status, raw = pinned_https_post(
                self.host, self.port, path, body, headers, self.pinned_sha256)
            try:
                result = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                if status >= 400:
                    raise HikError(_http_failure(status, {}, path)) from exc
                raise HikError("Resposta do serviço não é JSON válido da OpenAPI.") from exc
        else:
            try:
                response = self.session.post(self.url + path, data=body,
                                             headers=headers, timeout=(7, 25),
                                             verify=self.verify)
            except requests.exceptions.SSLError as exc:
                raise HikError("Falha no certificado TLS ou nome/IP não corresponde ao certificado. "
                               "Configure CA/hostname ou fixe o certificado SHA-256 "
                               "após confirmar sua origem no servidor.") from exc
            except requests.exceptions.ConnectTimeout as exc:
                raise HikError(f"Timeout conectando a {self.host}:{self.port}. "
                               "Verifique IP, porta, firewall, rede/VPN e serviço OpenAPI.") from exc
            except requests.exceptions.ConnectionError as exc:
                raise HikError(f"Não foi possível conectar a {self.host}:{self.port}. "
                               "IP/porta inacessível ou serviço offline. "
                               "Use o diagnóstico de rede.") from exc
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                raise HikError(f"HikCentral HTTP {status}. Confira OpenAPI e autorização.") from exc
            except requests.exceptions.RequestException as exc:
                raise HikError(f"Falha HTTP no HikCentral: {type(exc).__name__}: {exc}") from exc
            status = response.status_code
            try:
                result = response.json()
            except ValueError as exc:
                if status >= 400:
                    raise HikError(_http_failure(status, {}, path)) from exc
                raise HikError("A porta respondeu, mas não retornou JSON da OpenAPI. "
                               "Confira host, porta e caminho do Gateway.") from exc
        if not isinstance(result, dict):
            raise HikError("Resposta OpenAPI inesperada (não é objeto JSON).")
        if status >= 400:
            raise HikError(_http_failure(status, result, path))
        response_status = result.get("ResponseStatus")
        if not isinstance(response_status, dict):
            response_status = {}
        code = str(result.get("code", response_status.get("ErrorCode", "0")))
        if code != "0" and code.lower() != "success":
            raise HikError(_http_failure(status, result, path, code))
        return result

    def access_levels(self) -> list[dict]:
        """Acesso do módulo VISITOR requer type=2; type=1 é funcionários."""
        found = []
        page = 1
        while page <= 30:
            data = self.post("/artemis/api/acs/v1/privilege/group", {
                "pageNo": page, "pageSize": 100, "type": 2
            }).get("data") or {}
            if not isinstance(data, dict):
                raise HikError("Lista de níveis inválida.")
            rows = data.get("list") or []
            for row in rows:
                if row.get("privilegeGroupId") and row.get("privilegeGroupName"):
                    found.append({"id": str(row["privilegeGroupId"]),
                                  "name": str(row["privilegeGroupName"]),
                                  "elements": row.get("ElementList") or []})
            if len(rows) < 100 or page * 100 >= int(data.get("total", 0)):
                return found
            page += 1
        raise HikError("Paginação de níveis incompleta; operação bloqueada.")

    def visitor_groups(self) -> list[str]:
        """Grupo real, quando endpoint disponível; chamada separada da prova OpenAPI."""
        data = self.post("/artemis/api/visitor/v1/visitorgroups", {
            "SearchCriteria": {"visitorGroupName": ""}
        }).get("data") or {}
        obj = data.get("VisitorGroupList") or data.get("visitorGroupList") or {}
        rows = (obj.get("VisitorGroup") or obj.get("visitorGroup") or obj.get("list") or []) \
            if isinstance(obj, dict) else []
        groups = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            base = (row.get("baseInfo") or row.get("BaseInfo")
                    or row.get("visitorGroupBaseInfo") or {})
            name = (base.get("name") or base.get("Name") or base.get("visitorGroupName")
                    or row.get("visitorGroupName") or row.get("VisitorGroupName")
                    or row.get("name"))
            if name and name not in groups:
                groups.append(name)
        if not groups:
            fields = ", ".join(sorted(data.keys())) if isinstance(data, dict) else type(data).__name__
            row_fields = ", ".join(sorted(rows[0].keys())) if rows and isinstance(rows[0], dict) else "nenhum"
            raise HikError("A consulta de grupos Visitor não retornou nomes utilizáveis. "
                           f"Campos: {fields}; campos do primeiro grupo: {row_fields}.")
        return groups

    def resolve_levels(self, requested: list[str]) -> tuple[list[dict], list[str]]:
        levels = self.access_levels()
        selected, doors = [], []
        for name in requested:
            # Nome real historicamente cadastrado com typo no Vale Visitor.
            remote_name = "SAIDA AQCUAVALE" if normalize(name) == normalize("SAIDA ACQUAVALE") else name
            matches = [row for row in levels if normalize(row["name"]) == normalize(remote_name)]
            if len(matches) != 1:
                raise HikError(f"Nível Visitor ausente/duplicado: {name}. "
                               "Confira nomes no HikCentral (type=2).")
            level = matches[0]
            ids = []
            for entry in level["elements"]:
                if isinstance(entry, dict):
                    value = (entry.get("Element") or {}).get("ID")
                    if value:
                        ids.append(str(value))
            if not ids:
                raise HikError(f"Nível {name} não possui catracas associadas.")
            selected.append(level)
            for door in ids:
                if door not in doors:
                    doors.append(door)
        if not selected:
            raise HikError("Selecione pelo menos um nível de acesso.")
        return selected, doors

    @staticmethod
    def access_info(levels: list[dict]) -> dict:
        return {"accessLevelList": [{
            "accessLevel": {
                "id": int(l["id"]) if l["id"].isdigit() else l["id"],
                "baseInfo": {"name": l["name"]}
            }
        } for l in levels]}

    def create_appointment(self, info: dict, levels: list[dict]) -> tuple[str, str]:
        """Fluxo já usado no Vale Visitor; reserva primeiro, sem duplicar check-in."""
        response = self.post("/artemis/api/visitor/v2/appointment", {
            "receptionistId": "",
            "appointStartTime": info["visitStartTime"],
            "appointEndTime": info["visitEndTime"],
            "visitReasonType": 4,
            "visitReasonDetail": "Ingresso diario",
            "accessInfo": self.access_info(levels),
            "visitorInfoList": info["visitorInfoList"]
        })
        result = response.get("data") or {}
        appointment = str(result.get("appointRecordId") or "").strip()
        visitor = str(result.get("visitorId") or "").strip()
        if not appointment or not visitor:
            raise HikError("Reserva enviada, mas resposta sem appointRecordId/visitorId. "
                           "Consulte o HikCentral ANTES de repetir.")
        return appointment, visitor

    def register_visitor(self, info: dict, levels: list[dict], appointment: str, visitor: str) -> str:
        """Check-in após reserva; retorna ID registro, distinto de visitorId."""
        entries = json.loads(json.dumps(info["visitorInfoList"]))
        for item in entries:
            item["VisitorInfo"]["visitorId"] = visitor
        response = self.post("/artemis/api/visitor/v1/registerment", {
            "appointId": appointment,
            "visitorId": visitor,
            "visitStartTime": info["visitStartTime"],
            "visitEndTime": info["visitEndTime"],
            "visitPurposeType": 4,
            "visitPurpose": "Ingresso diario",
            "accessInfo": self.access_info(levels),
            "visitorInfoList": entries
        })
        data = response.get("data") or {}
        record = str(data.get("appointRecordId") or data.get("recordId") or "").strip()
        if not record:
            raise HikError("Check-in enviado, mas registro não confirmado. "
                           "Confira o HikCentral antes de tentar novamente.")
        return record

    def assign_access(self, visitor_id: str, level_id: str) -> None:
        self.post("/artemis/api/acs/v1/privilege/group/single/addPersons", {
            "privilegeGroupId": level_id, "type": 2,
            "list": [{"id": visitor_id}]
        })

    def apply(self, visitor_id: str, doors: list[str]) -> None:
        if not visitor_id or not doors:
            raise HikError("IDs do visitante/catracas não foram recebidos.")
        self.post("/artemis/api/visitor/v1/auth/reapplication", {
            "orderId": "", "ImmediateDownload": 0,
            "personIds": visitor_id,
            "doorIndexCodes": ",".join(doors)
        })

    def delivery_report(self, visitor_id: str, door_ids: list[str]) -> dict:
        if not visitor_id or not door_ids:
            raise HikError("Visitante e IDs das catracas são obrigatórios para consultar a entrega.")
        data = self.post("/artemis/api/visitor/v1/person/ID/elementDownloadDetail",
                         {"id": visitor_id}).get("data") or {}
        if not isinstance(data, dict):
            raise HikError("Resposta de entrega do HikCentral tem formato inesperado.")
        return parse_delivery_details(data, door_ids)


def _http_failure(status: int, result: dict, path: str, code: str = "") -> str:
    response_status = result.get("ResponseStatus") or {}
    if not isinstance(response_status, dict):
        response_status = {}
    message = str(result.get("msg") or response_status.get("ErrorDescription") or "")
    code = str(code or result.get("code") or response_status.get("ErrorCode") or "")
    detail = (code + " " + message).casefold()
    if "60051" in detail:
        return "HikCentral recusou o cadastro: há um visitante/reserva conflitante para o documento ou período. Consulte o registro antes de tentar novamente."
    if any(term in detail for term in ("signature", "hmac", "sign error", "assinatura")):
        return f"Assinatura Artemis/HMAC recusada ({code or status}). Confira relógio, payload e assinatura."
    if any(term in detail for term in ("appkey", "app key", "appsecret", "app secret", "invalid key", "invalid secret")):
        return f"AppKey/AppSecret recusados pelo HikCentral ({code or status}). Confira as credenciais do parceiro OpenAPI."
    if status == 403 or any(term in detail for term in ("permission", "privilege", "forbidden", "not authorized", "sem permissão")):
        return f"Parceiro sem permissão OpenAPI para {path} ({code or status}). Solicite somente as permissões Visitor/ACS necessárias."
    if status == 404:
        return f"Endpoint Artemis inexistente (HTTP 404): {path}. Confira versão e módulo habilitado."
    if status == 401:
        return "Autenticação OpenAPI recusada (HTTP 401). Confira assinatura HMAC, horário do servidor e AppKey/AppSecret."
    if message:
        return f"HikCentral rejeitou a operação ({code or status}): {message[:300]}"
    return f"HikCentral HTTP {status} para {path}. Confira endpoint, autenticação e permissão OpenAPI."


def parse_delivery_details(data: dict, door_ids: list[str], *,
                           expect_face: bool = True, expect_card: bool = True) -> dict:
    """Summarize only per-door face/card delivery states; never return credential data."""
    detail_list = data.get("ElementDetailList") or {}
    if isinstance(detail_list, list):
        rows = detail_list
    elif isinstance(detail_list, dict):
        rows = detail_list.get("ElementDetail") or []
    else:
        rows = []
    if isinstance(rows, dict):
        rows = [rows]
    indexed = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            element_id = str(row.get("elementID") or row.get("ID") or "")
            if element_id:
                indexed[element_id] = row
    doors = []
    for raw_id in dict.fromkeys(str(value) for value in door_ids):
        detail = indexed.get(raw_id, {})
        element_statuses = detail.get("ElementStatus") or []
        if isinstance(element_statuses, dict):
            element_statuses = [element_statuses]
        element_status = element_statuses[0].get("elementStatus") if element_statuses and isinstance(element_statuses[0], dict) else None
        try:
            element_status = int(element_status) if element_status is not None else None
        except (TypeError, ValueError):
            element_status = None
        certificate_list = detail.get("CertificateStatusList") or {}
        certificate_rows = (certificate_list.get("CertificateStatus") or []
                            if isinstance(certificate_list, dict) else [])
        if isinstance(certificate_rows, dict):
            certificate_rows = [certificate_rows]
        face_state = "not_required" if not expect_face else "queued"
        card_state = "not_required" if not expect_card else "queued"
        all_certificates_confirmed = bool(certificate_rows)
        certificate_failed = False
        for cert in certificate_rows if isinstance(certificate_rows, list) else []:
            if not isinstance(cert, dict):
                continue
            try:
                kind, status = int(cert.get("type", -1)), int(cert.get("status", -1))
            except (TypeError, ValueError):
                continue
            if status != 0:
                all_certificates_confirmed = False
            if status == 2:
                certificate_failed = True
            state = "confirmed" if status == 0 else "failed" if status == 2 else "queued"
            if kind == 2 and expect_face:
                face_state = state
            elif kind in (0, 4) and expect_card:
                card_state = state
        failed = certificate_failed or element_status == 2 or "failed" in (face_state, card_state)
        confirmed = (element_status == 0
                     and all_certificates_confirmed
                     and (not expect_face or face_state == "confirmed")
                     and (not expect_card or card_state == "confirmed"))
        base = detail.get("BaseInfo") or {}
        if not isinstance(base, dict):
            base = {}
        element_name = base.get("Name") or detail.get("elementName") or raw_id
        doors.append({
            "id": raw_id,
            "name": str(element_name),
            "state": "confirmed" if confirmed else "failed" if failed else "queued",
            "faceState": face_state,
            "credentialState": card_state,
        })
    states = [door["state"] for door in doors]
    state = "failed" if "failed" in states else "confirmed" if doors and all(x == "confirmed" for x in states) else "queued"
    message = ("HikCentral confirmou face e pulseira em todas as catracas consultadas."
               if state == "confirmed" else "Uma ou mais catracas recusaram uma credencial. Consulte os detalhes."
               if state == "failed" else "Entrega ainda pendente ou sem confirmação em todas as catracas.")
    return {"state": state, "message": message, "doors": doors}
