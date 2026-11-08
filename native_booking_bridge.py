"""Authenticated, short-lived Web Client bridge for same-visitor reservations.

The operator signs in to an isolated Microsoft Edge profile. OpenAPI credentials
are never copied into this browser, and the bridge performs only read-only
preflight checks until the transaction calls its returned create callback.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
import re
import shutil
import tempfile
from urllib.parse import urlsplit

from edit_errors import EditorFieldError
from hikcentral import HikError, normalize_pin, read_cert_sha256
from visitor_profile import validate_visitor_remark


def _same_origin(request_url, expected_url):
    """Compare normalized origins, including implicit HTTPS/HTTP default ports."""
    requested = urlsplit(str(request_url or ""))
    expected = urlsplit(str(expected_url or ""))
    if requested.scheme.casefold() != expected.scheme.casefold():
        return False
    if not requested.hostname or requested.hostname.casefold() != (expected.hostname or "").casefold():
        return False
    default_ports = {"https": 443, "http": 80}
    requested_port = requested.port or default_ports.get(requested.scheme.casefold())
    expected_port = expected.port or default_ports.get(expected.scheme.casefold())
    return requested_port is not None and requested_port == expected_port


def build_appointment_request(profile, original, visitor_id, start, end, selected_levels,
                              first_name, last_name, remark, encrypt, *,
                              purpose_type=4, purpose_detail=""):
    """Build the native Visitor Information request to reserve one existing visitor."""
    vid = str(visitor_id or "").strip()
    old = (original or {}).get("AppointmentRecord") or original or {}
    visitor = old.get("Visitor") or {}
    profile_id = str((profile or {}).get("ID") or "").strip()
    old_id = str(old.get("ID") or "").strip()
    linked_id = str(visitor.get("ID") or "").strip()
    if not vid or vid != profile_id or (old and vid != linked_id):
        raise EditorFieldError({"visit": "O HikCentral não confirmou o mesmo Visitor ID na reserva e no perfil."})
    if old and not re.fullmatch(r"\d{1,64}", str(old_id)):
        raise EditorFieldError({"appointment": "A reserva original não retornou um ID válido no Web Client."})

    profile_base = (profile.get("BaseInfo") or {})
    old_base = (visitor.get("BaseInfo") or {})
    group_id = str(profile_base.get("VisitorGroupID") or "")
    old_group_id = str(old_base.get("VisitorGroupID") or "")
    if not group_id or (old and group_id != old_group_id):
        raise EditorFieldError({"group": "O Web Client não confirmou o grupo original do visitante."})

    existing_levels = ((visitor.get("AccessInfo") or {}).get("AccessLevelList") or {})
    existing_levels = existing_levels.get("AccessLevel") or []
    if isinstance(existing_levels, dict):
        existing_levels = [existing_levels]
    native_levels = [(x.get("ID") or x.get("id") or "",
                      str((x.get("BaseInfo") or x.get("baseInfo") or {}).get("Name") or
                          (x.get("BaseInfo") or x.get("baseInfo") or {}).get("name") or ""))
                     for x in existing_levels if isinstance(x, dict)]
    normalized_existing = sorted((str(level_id), name) for level_id, name in native_levels)
    normalized_selected = sorted((str(x.get("id") or ""), str(x.get("name") or ""))
                                 for x in selected_levels if isinstance(x, dict))
    if not normalized_selected or (old and normalized_existing != normalized_selected):
        raise EditorFieldError({"group": "O nível Visitor da reserva original mudou. Confirme o nível antes de continuar."})

    extend_info = visitor.get("ExtendInfo") or {}
    reason = extend_info.get("VisitorReasonInfo") or {}
    reason_id = reason.get("ID", extend_info.get("VisitorReason", purpose_type))
    reason_detail = reason.get("VisitorReasonDetail", extend_info.get("VisitorReasonDetail", purpose_detail))
    if reason_id is None or str(reason_id).strip() == "":
        raise EditorFieldError({"visit": "O Web Client não retornou o motivo da visita."})

    first = str(first_name or "").strip()
    last = str(last_name or "").strip()
    if len(first) < 2 or len(last) < 2:
        raise EditorFieldError({"first" if len(first) < 2 else "last": "Informe nome e sobrenome válidos."})
    remark = validate_visitor_remark(remark)
    identification = profile_base.get("IdentifiyCode") or ""
    visitor_base = {
        "VisitorGroupID": profile_base.get("VisitorGroupID"),
        "IdentificationType": profile_base.get("IdentificationType", "") if identification else "",
        "IdentifiyCode": identification,
        "PhotoUrl": "",
        "SmallPhotoUrl": "",
        "GivenName": encrypt(first),
        "FamilyName": encrypt(last),
        "FullName": encrypt(first + " " + last),
        "Gender": profile_base.get("Gender", 0),
        "Email": profile_base.get("Email") or "",
        "PhoneNum": profile_base.get("PhoneNum") or "",
        "BeginDate": str(start),
        "EndDate": str(end),
        "Description": encrypt(str(remark or "").strip()),
        "CompanyName": profile_base.get("CompanyName") or "",
        "LicenseplateCode": profile_base.get("LicenseplateCode") or "",
        "PersonType": profile_base.get("PersonType", 0),
        "Nation": profile_base.get("Nation", 0),
    }
    # The native form forwards AccessLevel.ID from the server as-is. OpenAPI
    # normalizes IDs to text for comparison, but the V2 write uses native IDs.
    native_ids = {str(level_id): level_id for level_id, _name in native_levels}
    levels = []
    for level_id, name in normalized_selected:
        raw_id = native_ids.get(level_id)
        if raw_id is None:
            raw_id = int(level_id) if re.fullmatch(r"[0-9]{1,15}", level_id) else level_id
        levels.append({"ID": raw_id, "BaseInfo": {"Name": name}})
    original_access = visitor.get("AccessInfo") or {}
    qr_valid = original_access.get("QRCodeValidNum", 0)
    interviewee = extend_info.get("Interviewee") or extend_info.get("IntervieweeInfo") or {}
    interviewee_id = interviewee.get("ID") if isinstance(interviewee, dict) else None
    additional = profile.get("AdditionalInfo")
    if additional is None:
        additional = visitor.get("AdditionalInfo") or []
    return {
        "VisitorList": {
            "Visitor": [{
                "ID": profile.get("ID"),
                "BaseInfo": visitor_base,
                "AdditionalInfo": additional,
                "ExtendInfo": {
                    "Interviewee": {"ID": interviewee_id if interviewee_id not in (None, "") else -1},
                    "VisitorReason": reason_id,
                    "VisitorReasonDetail": reason_detail or "",
                },
                "QRCodeValidNum": qr_valid,
                "AccessInfo": {
                    "ElectrostaticDetectionType": original_access.get("ElectrostaticDetectionType", 0),
                    "QRCodeValidNum": qr_valid,
                    "AccessLevelList": {"AccessLevel": levels},
                },
            }],
        }
    }


# The native ISAPI transport adds the authenticated Web Client's AppendInfo token.
_READ_PERMISSIONS = r"""async () => {
  const isapi = window.delegate_library('f803799d0').default;
  const status = await isapi.get({
    url:'/ISAPI/Bumblebee/Platform/V1/Permission/Security/UserPermission',
    showLoading:false, errorResponse:() => false, error:() => false,
  });
  const user = status?.Data?.UserPermission || {};
  const codes = user.WebClientPermission ?? user.webClientPermission;
  return {ready:Number(status?.ErrorCode) === 0 && codes != null,
    canCreate:String(codes ?? '').split(',').some(code => code.trim() === '623')};
}"""

_LOGIN_READY = r"""async () => {
  if (typeof window.__valeReq !== 'function') {
    const queue = window.webpackChunkHCPModule_Visitor;
    if (!queue || typeof queue.push !== 'function') return {ready:false};
    queue.push([[`vale-bridge-${Date.now()}`], {}, require => { window.__valeReq = require; }]);
  }
  if (typeof window.__valeReq?.m?.[6337] !== 'function') return {ready:false};
  try {
    return await (""" + _READ_PERMISSIONS + r""")();
  } catch (_) { return {ready:false}; }
}"""

_PREPARE = r"""async args => {
  const require = window.__valeReq;
  if (typeof require !== 'function') throw new Error('O Web Client não carregou o módulo de visitantes.');
  const api = require(6337).A;
  const permissions = await (""" + _READ_PERMISSIONS + r""")();
  if (!permissions.ready || !permissions.canCreate)
    throw new Error('A conta do Web Client não tem permissão para criar a reserva substituta. Nenhuma etapa foi iniciada.');

  let original = null;
  if (args.expected_booking_id) {
    const oldResponse = await api.getAppointmentRecord(args.expected_booking_id, {});
    if (Number(oldResponse.ErrorCode) !== 0) throw new Error('Não foi possível confirmar a reserva original no Web Client.');
    original = oldResponse.Data?.AppointmentRecord;
    if (!original || String(original.ID) !== String(args.expected_booking_id) ||
        String(original.Visitor?.ID) !== String(args.visitor_id))
      throw new Error('A reserva original não está vinculada ao mesmo Visitor ID no Web Client.');
  }

  const personResponse = await api.getVisitorInfo({VisitorListRequest:{
    SearchCriteria:{VisitorIDList:String(args.visitor_id)}, Field:'CardList,FingerPrintList'
  }});
  if (Number(personResponse.ErrorCode) !== 0) throw new Error('Não foi possível confirmar o perfil em Visitor Information.');
  const rows = personResponse.Data?.VisitorList?.Visitor;
  const profile = Array.isArray(rows) ? rows.find(x=>String(x.ID)===String(args.visitor_id)) : rows;
  if (!profile || String(profile.ID) !== String(args.visitor_id))
    throw new Error('Visitor Information não retornou o perfil selecionado.');

  const oldBase = original?.Visitor?.BaseInfo || {};
  const base = profile.BaseInfo || {};
  if (original && String(base.VisitorGroupID || '') !== String(oldBase.VisitorGroupID || ''))
    throw new Error('Visitor Information e a reserva original retornaram grupos diferentes.');
  const findLevels = v => {
    let rows = v?.AccessInfo?.AccessLevelList?.AccessLevel || [];
    if (!Array.isArray(rows)) rows = [rows];
    return rows.filter(Boolean).map(x=>[String(x.ID ?? x.id ?? ''), String(x.BaseInfo?.Name ?? x.baseInfo?.name ?? '')]).sort();
  };
  const expected = (args.levels||[]).map(x=>[String(x.id),String(x.name)]).sort();
  if (original && JSON.stringify(findLevels(original.Visitor)) !== JSON.stringify(expected))
    throw new Error('O nível Visitor da reserva original mudou. Confirme o nível antes de continuar.');

  const detail = p => p ? p.split(',') : [];
  const watchCriteria = {BeginDate:args.start, EndDate:args.end,
    IdentifiyCode:base.IdentifiyCode || '', IdentificationType:base.IdentificationType || 0,
    CompanyName:base.CompanyName || '', Nation:base.Nation || 0,
    GivenName:base.GivenName || '', FamilyName:base.FamilyName || '', FullName:base.FullName || ''};
  const watchResponse = await api.getMonitorList({RequestWatchList:{SearchCriteria:{
    VisitorInfo:{BaseInfo:watchCriteria}
  }}});
  if (Number(watchResponse.ErrorCode) !== 0) throw new Error('Não foi possível conferir os alertas de segurança do Web Client.');
  const watch = watchResponse.Data?.ResponseWatchList?.WatchList;
  if (watch && (Array.isArray(watch) ? watch.length : true))
    throw new Error('O Web Client encontrou um alerta de segurança para este visitante e período. Confira antes de editar.');

  const encrypt = window.delegate_library?.('377caa329')?.default?.encrypt;
  if (typeof encrypt !== 'function') throw new Error('O Web Client não disponibilizou a proteção de dados necessária.');
  const encryptions = [args.first_name, args.last_name, `${args.first_name} ${args.last_name}`, args.remark]
    .map(v=>v ? encrypt(v) : '');
  return {profile:{ID:profile.ID, BaseInfo:base, AdditionalInfo:profile.AdditionalInfo},
    original:original ? {ID:original.ID, Visitor:original.Visitor} : null, encryptions};
}"""

_STORE_REQUEST = r"""async body => {
  window.__valePreparedAppointment = body;
  return true;
}"""

_CREATE = r"""async () => {
  const api = window.__valeReq(6337).A;
  const response = await api.addAppointVisitor(window.__valePreparedAppointment);
  const appointments = response?.Data?.AppointmentRecordList?.AppointmentRecord;
  const rows = Array.isArray(appointments) ? appointments : appointments ? [appointments] : [];
  if (Number(response?.ErrorCode) !== 0 || rows.length !== 1)
    throw new Error('O Web Client não confirmou a criação da reserva substituta. Confira Visitor Reservation antes de repetir.');
  const row = rows[0];
  const result = {visitor_id:String(row.Visitor?.ID || ''),
    appointment_id:String(row.ID || ''), code:String(row.AppointCode || ''),
    entry_at:String(row.Visitor?.BaseInfo?.BeginDate || ''),
    exit_at:String(row.Visitor?.BaseInfo?.EndDate || '')};
  if (!result.visitor_id || !result.appointment_id || !result.code)
    throw new Error('O Web Client respondeu sem confirmar o ID e o código da reserva. Consulte Visitor Reservation.');
  return result;
}"""


class BookingBridge(AbstractContextManager):
    def __init__(self, host, port, *, timeout_seconds=180, cert_pin_sha256=""):
        self.host = str(host or "").strip().strip("[]")
        self.port = int(port)
        self.timeout_seconds = max(30, min(int(timeout_seconds), 600))
        self.cert_pin_sha256 = normalize_pin(cert_pin_sha256 or "")
        self._playwright = None
        self._context = None
        self._page = None
        self._profile_dir = None
        self._origin = None

    def __enter__(self):
        if not self.host or not 1 <= self.port <= 65535:
            raise HikError("Confira o endereço do HikCentral nas Configurações.")
        if not self.cert_pin_sha256:
            raise HikError("Configure a impressão digital SHA-256 do HikCentral antes de criar uma reserva.")
        presented = read_cert_sha256(self.host, self.port)
        if presented != self.cert_pin_sha256:
            raise HikError("O certificado do HikCentral mudou. Nenhuma credencial do Web Client foi enviada.")
        self._origin = f"https://{self.host}:{self.port}"
        self._profile_dir = tempfile.mkdtemp(prefix="vale-hikcentral-")
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._context = self._playwright.chromium.launch_persistent_context(
                self._profile_dir, channel="msedge", headless=False,
                ignore_https_errors=True, service_workers="block",
                args=["--disable-sync", "--no-first-run", "--no-default-browser-check"],
            )
            self._context.route("**/*", self._route_same_origin)
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.goto(self._origin + "/#/portal", wait_until="domcontentloaded", timeout=30000)
            self._wait_for_operator()
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def _route_same_origin(self, route):
        try:
            if _same_origin(route.request.url, self._origin):
                route.continue_()
            else:
                route.abort()
        except Exception:
            route.abort()

    def _wait_for_operator(self):
        self._page.bring_to_front()
        deadline = __import__("time").monotonic() + self.timeout_seconds
        while __import__("time").monotonic() < deadline:
            try:
                result = self._page.evaluate(_LOGIN_READY)
                if result.get("ready"):
                    return
            except Exception:
                pass
            __import__("time").sleep(1)
        raise HikError("Entre no HikCentral na janela do Edge e abra Visitor > Visitor Reservation. "
                       "Nenhuma etapa da troca foi iniciada.")

    def prepare(self, visitor_id, start, end, selected_levels, *, expected_booking_id=None,
                first_name, last_name, remark, purpose_type=4, purpose_detail=""):
        if not self._page or not _same_origin(self._page.url, self._origin):
            raise HikError("A sessão autenticada do Web Client não está disponível.")
        args = {"visitor_id":str(visitor_id), "start":str(start), "end":str(end),
                "levels":[{"id":str(x["id"]), "name":str(x["name"])} for x in selected_levels],
                "expected_booking_id":str(expected_booking_id), "first_name":str(first_name),
                "last_name":str(last_name), "remark":str(remark or ""),
                "purpose_type":purpose_type, "purpose_detail":str(purpose_detail or "")}
        if expected_booking_id is None:
            args["expected_booking_id"] = ""
        try:
            prepared = self._page.evaluate(_PREPARE, args)
            encrypted = prepared["encryptions"]
            encrypted_fields = iter(encrypted)
            body = build_appointment_request(
                prepared["profile"], prepared["original"], args["visitor_id"], args["start"], args["end"],
                args["levels"], args["first_name"], args["last_name"], args["remark"],
                lambda _value: next(encrypted_fields),
                purpose_type=args["purpose_type"], purpose_detail=args["purpose_detail"],
            )
            self._page.evaluate(_STORE_REQUEST, body)
        except EditorFieldError:
            raise
        except Exception as exc:
            message = str(exc)
            if "623" in message or "permissão" in message.casefold():
                raise EditorFieldError({"appointment": message}) from exc
            raise HikError(message or "Não foi possível preparar a reserva no Web Client.") from exc

        def create():
            result = self._page.evaluate(_CREATE)
            result["visitor_id"] = str(result.get("visitor_id") or "")
            result["automatic_checkin"] = False
            return result

        return create

    def __exit__(self, exc_type, exc, traceback):
        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        if self._profile_dir:
            try:
                shutil.rmtree(self._profile_dir, ignore_errors=True)
            finally:
                self._profile_dir = None
        return False


def open_booking_bridge(host, port, *, timeout_seconds=180, cert_pin_sha256=""):
    return BookingBridge(host, port, timeout_seconds=timeout_seconds,
                         cert_pin_sha256=cert_pin_sha256)
