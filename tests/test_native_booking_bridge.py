from copy import deepcopy
import shutil
import subprocess
from unittest.mock import Mock

import pytest

from edit_errors import EditorFieldError
from native_booking_bridge import BookingBridge, _CREATE, _LOGIN_READY, _PREPARE, _same_origin, build_appointment_request


@pytest.mark.parametrize("request_url,expected_url,allowed", [
    ("https://192.168.102.100/#/portal", "https://192.168.102.100:443", True),
    ("https://192.168.102.100:443/#/portal", "https://192.168.102.100", True),
    ("https://192.168.102.100:8443/#/portal", "https://192.168.102.100:443", False),
    ("http://192.168.102.100/#/portal", "https://192.168.102.100:443", False),
    ("https://other.example/#/portal", "https://192.168.102.100:443", False),
    ("https://evil192.168.102.100/#/portal", "https://192.168.102.100:443", False),
])
def test_same_origin_allows_normalized_default_port_without_opening_external_hosts(
        request_url, expected_url, allowed):
    assert _same_origin(request_url, expected_url) is allowed


def test_route_handler_allows_hikcentral_request_when_edge_omits_https_443():
    class Request:
        url = "https://192.168.102.100/#/portal"

    class Route:
        request = Request()
        continued = False
        aborted = False

        def continue_(self):
            self.continued = True

        def abort(self):
            self.aborted = True

    bridge = BookingBridge("192.168.102.100", 443, cert_pin_sha256="")
    bridge._origin = "https://192.168.102.100:443"
    route = Route()
    bridge._route_same_origin(route)
    assert route.continued is True
    assert route.aborted is False


@pytest.fixture
def booking_metadata():
    profile = {
        "ID": 1865,
        "BaseInfo": {
            "VisitorGroupID": 3, "Gender": 0, "Email": "", "PhoneNum": "",
            "IdentificationType": 0, "IdentifiyCode": "", "CompanyName": "Vale",
            "LicenseplateCode": "", "PersonType": 0, "Nation": 0,
        },
        "AdditionalInfo": [{"Type": 22, "Value": "0"}],
    }
    original = {
        "ID": "774255947668258816",
        "Visitor": {
            "ID": 1865,
            "BaseInfo": {"VisitorGroupID": 3},
            "AccessInfo": {
                "AccessLevelList": {"AccessLevel": [{
                    "ID": 7, "BaseInfo": {"Name": "VISITANTE_VALE"},
                }]},
                "ElectrostaticDetectionType": 0,
                "QRCodeValidNum": 0,
            },
            "ExtendInfo": {"VisitorReasonInfo": {
                "ID": 4, "VisitorReasonDetail": "Hospedagem / Day use",
            }},
            "AdditionalInfo": [{"Type": 22, "Value": "0"}],
        },
    }
    return profile, original


def _build(profile, original, *, vid="1865", levels=None):
    return build_appointment_request(
        profile, original, vid, "2026-11-16T00:00:00-03:00", "2026-11-17T23:59:59-03:00",
        levels or [{"id": "7", "name": "VISITANTE_VALE"}],
        "Teste", "Sobrenome", "321", lambda value: "enc:" + value,
    )


def test_builds_native_v2_request_for_existing_visitor_with_new_dates_and_access(booking_metadata):
    profile, original = booking_metadata
    request = _build(profile, original)
    visitor, = request["VisitorList"]["Visitor"]

    assert visitor["ID"] == 1865
    assert visitor["BaseInfo"]["VisitorGroupID"] == 3
    assert visitor["BaseInfo"]["GivenName"] == "enc:Teste"
    assert visitor["BaseInfo"]["FamilyName"] == "enc:Sobrenome"
    assert visitor["BaseInfo"]["FullName"] == "enc:Teste Sobrenome"
    assert visitor["BaseInfo"]["Description"] == "enc:321"
    assert visitor["AdditionalInfo"] == profile["AdditionalInfo"]
    assert visitor["ExtendInfo"]["VisitorReason"] == 4
    assert visitor["ExtendInfo"]["VisitorReasonDetail"] == "Hospedagem / Day use"
    assert visitor["BaseInfo"]["BeginDate"] == "2026-11-16T00:00:00-03:00"
    assert visitor["BaseInfo"]["EndDate"] == "2026-11-17T23:59:59-03:00"
    assert visitor["AccessInfo"]["AccessLevelList"] == {
        "AccessLevel": [{"ID": 7, "BaseInfo": {"Name": "VISITANTE_VALE"}}],
    }
    assert "cards" not in visitor and "faces" not in visitor


def test_create_recovery_uses_current_profile_without_deleted_booking(booking_metadata):
    profile, _original = booking_metadata
    request = _build(profile, None)
    visitor, = request["VisitorList"]["Visitor"]
    assert visitor["ID"] == 1865
    assert visitor["ExtendInfo"]["VisitorReason"] == 4
    assert visitor["AccessInfo"]["AccessLevelList"]["AccessLevel"] == [
        {"ID": 7, "BaseInfo": {"Name": "VISITANTE_VALE"}},
    ]


def test_prepare_accepts_hikcentral_page_when_edge_omits_https_443(booking_metadata):
    profile, original = booking_metadata
    bridge = BookingBridge("192.168.102.100", 443)
    bridge._origin = "https://192.168.102.100:443"
    bridge._page = Mock(url="https://192.168.102.100/#/portal")
    bridge._page.evaluate.side_effect = [
        {"profile": profile, "original": original, "encryptions": ["F", "L", "FL", "R"]},
        True,
        {"visitor_id": 1865, "appointment_id": "123", "code": "1234"},
    ]

    create = bridge.prepare(
        "1865", "2026-11-16T00:00:00-03:00", "2026-11-17T23:59:59-03:00",
        [{"id": "7", "name": "VISITANTE_VALE"}], expected_booking_id=original["ID"],
        first_name="Teste", last_name="Sobrenome", remark="321",
    )
    prepared_body = bridge._page.evaluate.call_args.args[1]
    assert prepared_body["VisitorList"]["Visitor"][0]["ID"] == 1865
    assert create()["visitor_id"] == "1865"


def _run_node(script):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js necessário para executar o JavaScript do Web Client")
    result = subprocess.run([node, "-"], input=script, encoding="utf-8", capture_output=True,
                            timeout=10, check=False)
    assert result.returncode == 0, result.stderr


def test_login_ready_waits_for_visitor_api_to_load():
    _run_node("const loginReady = " + _LOGIN_READY + ";\n" + r"""
const assert = require('node:assert/strict');
const visitorRequire = () => {};
visitorRequire.m = {};
let permissionReads = 0;
global.fetch = () => assert.fail('Permission checks must use the authenticated Web Client transport');
global.window = {
  webpackChunkHCPModule_Visitor: {push: chunk => chunk[2](visitorRequire)},
  delegate_library: id => {
    assert.equal(id, 'f803799d0');
    return {default: {get: async options => {
      assert.equal(options.url, '/ISAPI/Bumblebee/Platform/V1/Permission/Security/UserPermission');
      permissionReads++;
      return {ErrorCode: 0, Data: {UserPermission: {webClientPermission: '20, 623'}}};
    }}};
  },
};
(async () => {
  assert.equal((await loginReady()).ready, false);
  assert.equal(permissionReads, 0);
  visitorRequire.m[6337] = () => {};
  assert.deepEqual(await loginReady(), {ready: true, canCreate: true});
  assert.equal(permissionReads, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_prepare_script_can_recover_without_an_original_booking():
    _run_node("const prepare = " + _PREPARE + ";\n" + r"""
const assert = require('node:assert/strict');
const profile = {ID: '1865', BaseInfo: {VisitorGroupID: 3}};
global.fetch = () => assert.fail('Permission checks must use the authenticated Web Client transport');
global.window = {
  __valeReq: () => ({A: {
    getAppointmentRecord: () => assert.fail('No original booking to load'),
    getVisitorInfo: async () => ({ErrorCode: 0, Data: {VisitorList: {Visitor: [profile]}}}),
    getMonitorList: async () => ({ErrorCode: 0, Data: {ResponseWatchList: {}}}),
  }}),
  delegate_library: id => id === 'f803799d0'
    ? {default: {get: async () => ({ErrorCode: 0, Data: {UserPermission: {WebClientPermission: '623'}}})}}
    : {default: {encrypt: value => 'enc:' + value}},
};
prepare({visitor_id: '1865', expected_booking_id: '', first_name: 'Teste',
  last_name: 'Sobrenome', remark: '321'}).then(result => {
    assert.equal(result.original, null);
    assert.equal(result.profile.ID, '1865');
    assert.deepEqual(result.encryptions, ['enc:Teste', 'enc:Sobrenome', 'enc:Teste Sobrenome', 'enc:321']);
  }).catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_create_script_uses_single_reservation_route_and_reads_native_result():
    _run_node("const create = " + _CREATE + ";\n" + r"""
const assert = require('node:assert/strict');
const prepared = {VisitorList: {Visitor: [{ID: 1865}]}};
const native = {ID: '774255947668258816', AppointCode: '8240', Visitor: {ID: 1865}};
let writes = 0;
global.window = {
  __valePreparedAppointment: prepared,
  __valeReq: () => ({A: {addAppointVisitor: async body => {
    assert.equal(body, prepared);
    writes++;
    return {ErrorCode: 0, Data: {AppointmentRecordList: {AppointmentRecord: [native]}}};
  }}}),
};
create().then(result => {
  assert.equal(writes, 1);
  assert.equal(result.appointment_id, native.ID);
  assert.equal(result.visitor_id, '1865');
  assert.equal(result.code, '8240');
}).catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_rejects_a_different_visitor_or_a_changed_original_level(booking_metadata):
    profile, original = booking_metadata
    with pytest.raises(EditorFieldError, match="Visitor ID"):
        _build(profile, original, vid="9999")
    with pytest.raises(EditorFieldError, match="nível Visitor"):
        _build(profile, original, levels=[{"id": "8", "name": "VISITANTE_SERRA"}])


def test_rejects_profile_and_booking_group_disagreement(booking_metadata):
    profile, original = booking_metadata
    changed = deepcopy(profile)
    changed["BaseInfo"]["VisitorGroupID"] = 4
    with pytest.raises(EditorFieldError, match="grupo original"):
        _build(changed, original)
