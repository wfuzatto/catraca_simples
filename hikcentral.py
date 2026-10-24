"""HikCentral OpenAPI - adaptado do Vale Visitor funcional (hcp.php)."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
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


def network_diagnostics(host: str, port: int, ca_file: str = "",
                        timeout: float = 3.0) -> str:
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
                 ca_file: str = "", session=None):
        if not host or not key or not secret:
            raise HikError("Configure host e credenciais OpenAPI (AppKey/AppSecret).")
        if "://" in host or "/" in host or "\\" in host:
            raise HikError("Host inválido; informe apenas IP ou DNS.")
        self.host, self.port = host, int(port)
        self.url = f"https://{host}:{int(port)}"
        self.key, self.secret = key, secret
        self.verify = ca_file if ca_file else True
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
        try:
            response = self.session.post(self.url + path, data=body,
                                         headers=headers, timeout=(7, 25),
                                         verify=self.verify)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.SSLError as exc:
            raise HikError("Falha no certificado TLS ou nome/IP não corresponde ao certificado. "
                           "Use o certificado/hostname válido e a CA PEM correspondente.") from exc
        except requests.exceptions.ConnectTimeout as exc:
            raise HikError(f"Timeout conectando a {self.host}:{self.port}. "
                           "Verifique IP, porta, firewall, rede/VPN e serviço OpenAPI.") from exc
        except requests.exceptions.ConnectionError as exc:
            raise HikError(f"Não foi possível conectar a {self.host}:{self.port}. "
                           "IP/porta inacessível, conexão recusada ou serviço offline. "
                           "Use o botão Diagnóstico de rede para verificar portas 443/37443.") from exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            raise HikError(f"HikCentral HTTP {status}. Confira endereço, OpenAPI e autorização do partner.") from exc
        except requests.exceptions.RequestException as exc:
            raise HikError(f"Falha HTTP no HikCentral: {type(exc).__name__}: {exc}") from exc
        except ValueError as exc:
            raise HikError("A porta respondeu, mas não retornou JSON da OpenAPI. "
                           "Confira host, porta e caminho do Gateway.") from exc
        if not isinstance(result, dict):
            raise HikError("Resposta OpenAPI inesperada (não é objeto JSON).")
        code = str(result.get("code", result.get("ResponseStatus", {}).get("ErrorCode", "0")))
        if code != "0" and code.lower() != "success":
            msg = result.get("msg") or result.get("ResponseStatus", {}).get("ErrorDescription", "Erro sem descrição")
            raise HikError(f"HikCentral rejeitou a operação ({code}): {str(msg)[:300]}")
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
        obj = data.get("VisitorGroupList") or {}
        rows = obj.get("VisitorGroup") or []
        groups = []
        for row in rows:
            name = (row.get("baseInfo") or {}).get("name") or row.get("name")
            if name and name not in groups:
                groups.append(name)
        if not groups:
            raise HikError("A consulta de grupos Visitor retornou lista vazia ou outro formato.")
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
