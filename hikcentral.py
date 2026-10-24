"""Cliente OpenAPI Artemis, usando o fluxo nativo Visitor."""
from __future__ import annotations
import base64
import hashlib
import hmac
import time
import unicodedata
import requests

class HikError(RuntimeError):
    pass


def normalize(name: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFD", name)
                            if unicodedata.category(c) != "Mn").casefold().split())


def sign(path: str, key: str, secret: str, timestamp: str) -> str:
    # Compatível com HikSigner.kt do projeto Android existente.
    canonical = "POST\n*/*\napplication/json\nx-ca-key:" + key + (
        "\nx-ca-timestamp:" + timestamp + "\n" + path)
    digest = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class HikCentral:
    def __init__(self, host: str, port: int, key: str, secret: str, ca_file: str = "",
                 session=None):
        if not host or not key or not secret:
            raise HikError("Configure host e credenciais OpenAPI (AppKey/AppSecret).")
        if "://" in host or "/" in host or "\\" in host:
            raise HikError("Host inválido; informe apenas IP ou DNS.")
        self.url = f"https://{host}:{int(port)}"
        self.key, self.secret = key, secret
        self.verify = ca_file if ca_file else True
        self.session = session or requests.Session()

    def post(self, path: str, payload: dict | None = None) -> dict:
        timestamp = str(int(time.time() * 1000))
        headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "X-Ca-Key": self.key,
            "X-Ca-Timestamp": timestamp,
            "X-Ca-Signature-Headers": "x-ca-key,x-ca-timestamp",
            "X-Ca-Signature": sign(path, self.key, self.secret, timestamp)
        }
        try:
            response = self.session.post(self.url + path, json=payload or {},
                                         headers=headers, timeout=(7, 25), verify=self.verify)
            response.raise_for_status()
            result = response.json()
        except requests.exceptions.SSLError as e:
            raise HikError("Certificado TLS inválido ou nome/IP não corresponde ao certificado. "
                           "Configure o certificado e DNS adequados.") from e
        except requests.exceptions.RequestException as e:
            raise HikError(f"Falha de comunicação com HikCentral: {type(e).__name__}") from e
        except ValueError as e:
            raise HikError("Resposta não é JSON válido da OpenAPI; confira host/porta.") from e
        if not isinstance(result, dict) or str(result.get("code", "")) != "0":
            code = result.get("code", "?") if isinstance(result, dict) else "?"
            msg = result.get("msg", "resposta inesperada") if isinstance(result, dict) else "formato inválido"
            raise HikError(f"HikCentral recusou a chamada ({code}): {str(msg)[:200]}")
        return result

    def visitor_groups(self) -> list[str]:
        result = self.post("/artemis/api/visitor/v1/visitorgroups",
                           {"SearchCriteria": {"visitorGroupName": ""}})
        data = result.get("data") or {}
        group_container = data.get("VisitorGroupList") or {}
        rows = group_container.get("VisitorGroup") or []
        groups = []
        for row in rows:
            name = (row.get("baseInfo") or {}).get("name") or row.get("name")
            if name and name not in groups:
                groups.append(name)
        return groups

    def access_levels(self) -> list[dict]:
        found = []
        page = 1
        for _ in range(20):
            data = self.post("/artemis/api/acs/v1/privilege/group",
                             {"pageNo": page, "pageSize": 500, "type": 1}).get("data") or {}
            rows = data.get("list") or []
            for row in rows:
                if row.get("privilegeGroupId") and row.get("privilegeGroupName"):
                    found.append({"id": str(row["privilegeGroupId"]),
                                  "name": str(row["privilegeGroupName"])})
            if len(rows) < 500:
                break
            page += 1
        return found

    def resolve_access_ids(self, names: list[str]) -> list[str]:
        levels = self.access_levels()
        ids = []
        for name in names:
            matched = [g["id"] for g in levels if normalize(g["name"]) == normalize(name)]
            if len(matched) != 1:
                raise HikError(f"Nível de acesso ausente/ambíguo: {name}. "
                               "Verifique níveis reais antes de cadastrar.")
            ids.append(matched[0])
        return ids

    def create_visitor(self, payload: dict) -> str:
        result = self.post("/artemis/api/visitor/v1/registerment", payload)
        data = result.get("data") or {}
        visitor_id = str(data.get("visitorId", "")).strip()
        if not visitor_id:
            raise HikError("HikCentral respondeu sucesso, mas sem visitorId. "
                           "Consulte o painel ANTES de reenviar para evitar duplicidade.")
        return visitor_id

    def assign_access(self, visitor_id: str, level_id: str) -> None:
        self.post("/artemis/api/acs/v1/privilege/group/single/addPersons", {
            "privilegeGroupId": level_id, "type": 1,
            "list": [{"id": visitor_id}]
        })

    def apply(self) -> None:
        self.post("/artemis/api/visitor/v1/auth/reapplication", {})
