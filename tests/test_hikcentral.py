"""Testes com respostas simuladas: sem chamadas ao HikCentral real."""
import base64
import hashlib
import hmac
import pytest
from hikcentral import HikCentral, HikError, normalize, sign


class Response:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class MockSession:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        data = self.data.pop(0) if isinstance(self.data, list) else self.data
        return Response(data, self.status_code)


def client(response):
    session = MockSession(response)
    return HikCentral("192.168.100.102", 443, "ak", "sk", session=session), session


def test_signer_matches_vale_visitor_php():
    path, key, secret, timestamp = "/artemis/api/visitor/v2/appointment", "ak", "sk", "123"
    content_md5 = base64.b64encode(hashlib.md5(b"{}").digest()).decode()
    canonical = ("POST\n*/*\n" + content_md5 +
                 "\napplication/json\nx-ca-key:ak\nx-ca-nonce:ABC\nx-ca-timestamp:123\n" + path)
    expected = base64.b64encode(hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).digest()).decode()
    assert sign(path, key, secret, timestamp, content_md5, "ABC") == expected


def test_openapi_request_headers():
    api, session = client({"code": "0", "data": {}})
    api.post("/artemis/api/acs/v1/privilege/group", {"type": 2})
    headers = session.calls[0][1]["headers"]
    assert headers["userId"] == "admin"
    assert headers["x-ca-signature-headers"] == "x-ca-key,x-ca-nonce,x-ca-timestamp"
    assert headers["Content-MD5"] == base64.b64encode(hashlib.md5(b'{"type":2}').digest()).decode()
    assert session.calls[0][1]["verify"] is True


def test_visitor_access_levels_type_two():
    result = {"code": "0", "data": {"list": [{
        "privilegeGroupId": "23",
        "privilegeGroupName": "SAIDA AQCUAVALE",
        "ElementList": [{"Element": {"ID": "DOOR-1"}}],
    }], "total": 1}}
    api, session = client(result)
    levels, doors = api.resolve_levels(["SAIDA ACQUAVALE"])
    assert len(levels) == 1
    assert doors == ["DOOR-1"]
    assert b'"type":2' in session.calls[0][1]["data"]


def test_groups_are_fetched_from_real_visitor_endpoint():
    api, session = client({"code": "0", "data": {"VisitorGroupList": {"VisitorGroup": [
        {"baseInfo": {"Name": "Day use"}}, {"baseInfo": {"Name": "Visitors"}},
        {"baseInfo": {"Name": "Visitors"}}
    ]}}})
    assert api.visitor_groups() == ["Day use", "Visitors"]
    assert session.calls[0][0].endswith("/artemis/api/visitor/v1/visitorgroups")


def test_reserve_register_reapply_flow():
    api, session = client([
        {"code": "0", "data": {"appointRecordId": "appt-1", "visitorId": "v-1"}},
        {"code": "0", "data": {"appointRecordId": "checkin-1"}},
        {"code": "0", "data": {}},
        {"code": "0", "data": {}},
    ])
    levels = [{"id": "23", "name": "Entrada", "elements": [{"Element": {"ID": "door-1"}}]}]
    info = {
        "visitStartTime": "2026-10-24T08:00:00-03:00",
        "visitEndTime": "2026-10-24T23:59:59-03:00",
        "visitorInfoList": [{"VisitorInfo": {
            "visitorGivenName": "Teste", "visitorFamilyName": "Local",
            "visitorGroupName": "Day use", "remark": "R-123",
            "faces": [{"faceData": "encoded"}],
            "cards": [{"cardNo": "123"}]
        }}]
    }
    appoint, visitor = api.create_appointment(info, levels)
    assert (appoint, visitor) == ("appt-1", "v-1")
    assert api.register_visitor(info, levels, appoint, visitor) == "checkin-1"
    api.assign_access(visitor, "23")
    api.apply(visitor, ["door-1"])
    endpoints = [call[0].split(":443")[-1] for call in session.calls]
    assert endpoints == [
        "/artemis/api/visitor/v2/appointment",
        "/artemis/api/visitor/v1/registerment",
        "/artemis/api/acs/v1/privilege/group/single/addPersons",
        "/artemis/api/visitor/v1/auth/reapplication"
    ]
    import json
    assert json.loads(session.calls[2][1]["data"])["type"] == 2
    assert json.loads(session.calls[3][1]["data"])["doorIndexCodes"] == "door-1"


def test_no_appointment_ids_blocks_duplicates():
    api, session = client({"code": "0", "data": {}})
    with pytest.raises(HikError, match="appointRecordId"):
        api.create_appointment({"visitStartTime": "t1", "visitEndTime": "t2",
                                "visitorInfoList": []}, [])


def test_api_errors_explain_signature_permission_and_endpoint():
    for result, pattern in [
        ({"code": "1001", "msg": "signature invalid"}, "Artemis/HMAC"),
        ({"code": "1002", "msg": "AppKey invalid"}, "AppKey/AppSecret recusados"),
        ({"code": "403", "msg": "permission denied"}, "sem permissão OpenAPI"),
    ]:
        api, _ = client(result)
        with pytest.raises(HikError, match=pattern):
            api.post("/artemis/api/acs/v1/privilege/group", {"type": 2})
    session = MockSession({}, status_code=404)
    api = HikCentral("127.0.0.1", 443, "ak", "sk", session=session)
    with pytest.raises(HikError, match="Endpoint Artemis inexistente"):
        api.post("/artemis/api/acs/v1/privilege/group")


def test_unexpected_openapi_shape_is_rejected():
    api, _ = client(["not", "an", "object"])
    with pytest.raises(HikError, match="não é objeto JSON"):
        api.post("/artemis/api/visitor/v1/visitorgroups")


def test_delivery_report_requires_face_and_card_on_each_door():
    from hikcentral import parse_delivery_details
    data = {"ElementDetailList": {"ElementDetail": [
        {"elementID": "door-1", "BaseInfo": {"Name": "Porta A"},
         "ElementStatus": [{"elementStatus": 0}],
         "CertificateStatusList": {"CertificateStatus": [
             {"type": 2, "status": 0}, {"type": 0, "status": 0}]}},
        {"elementID": "door-2", "BaseInfo": {"Name": "Porta B"},
         "ElementStatus": [{"elementStatus": 0}],
         "CertificateStatusList": {"CertificateStatus": [
             {"type": 2, "status": 0}, {"type": 4, "status": 2}]}}
    ]}}
    report = parse_delivery_details(data, ["door-1", "door-2"])
    assert report["state"] == "failed"
    assert report["doors"][0]["state"] == "confirmed"
    assert report["doors"][0]["name"] == "Porta A"
    assert report["doors"][1]["credentialState"] == "failed"
    assert "cardNo" not in repr(report)


def test_delivery_report_checks_only_requested_credential():
    from hikcentral import parse_delivery_details
    data = {"ElementDetailList": {"ElementDetail": [
        {"elementID": "face-door", "ElementStatus": [{"elementStatus": 0}],
         "CertificateStatusList": {"CertificateStatus": [
             {"type": 2, "status": 0}, {"type": 4, "status": 2}]}},
        {"elementID": "card-door", "ElementStatus": [{"elementStatus": 0}],
         "CertificateStatusList": {"CertificateStatus": [
             {"type": 2, "status": 2}, {"type": 4, "status": 0}]}}
    ]}}
    face = parse_delivery_details(data, ["face-door"], expect_face=True, expect_card=False)
    assert face["state"] == "confirmed"
    assert face["doors"][0]["faceState"] == "confirmed"
    assert face["doors"][0]["credentialState"] == "not_required"
    assert "reconhecimento facial" in face["message"]

    card = parse_delivery_details(data, ["card-door"], expect_face=False, expect_card=True)
    assert card["state"] == "confirmed"
    assert card["doors"][0]["faceState"] == "not_required"
    assert card["doors"][0]["credentialState"] == "confirmed"
    assert "pulseira" in card["message"]


def test_delivery_report_reads_only_expected_door_ids():
    from hikcentral import parse_delivery_details
    report = parse_delivery_details({}, ["door-1"])
    assert report["state"] == "queued"
    assert report["doors"] == [{"id": "door-1", "name": "door-1", "state": "queued",
                                "faceState": "queued", "credentialState": "queued"}]


def test_delivery_report_uses_visitor_id_and_report_endpoint():
    api, session = client({"code": "0", "data": {}})
    report = api.delivery_report("visitor-1", ["door-1"])
    assert report["state"] == "queued"
    url, call = session.calls[0]
    assert url.endswith("/artemis/api/visitor/v1/person/ID/elementDownloadDetail")
    assert call["data"] == b'{"id":"visitor-1"}'


def test_network_diagnostics_accepts_only_matching_pin(monkeypatch):
    from hikcentral import network_diagnostics

    class Socket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    monkeypatch.setattr("hikcentral.socket.getaddrinfo",
                        lambda host, port, type: [(None, None, None, None, ("127.0.0.1", port))])
    monkeypatch.setattr("hikcentral.socket.create_connection", lambda *a, **k: Socket())
    monkeypatch.setattr("hikcentral.read_cert_sha256", lambda *a, **k: "A" * 64)
    ok = network_diagnostics("127.0.0.1", 443, pinned_sha256="A" * 64)
    assert "TCP 443: ACESSÍVEL" in ok
    assert "corresponde" in ok
    mismatch = network_diagnostics("127.0.0.1", 443, pinned_sha256="B" * 64)
    assert "inválido ou alterado" in mismatch


def test_normalization():
    assert normalize("HÓSPEDES  VALE") == normalize("hospedes vale")


def test_cert_pin_rejects_mismatch_before_sending_secrets(monkeypatch):
    from unittest.mock import Mock
    from hikcentral import pinned_https_post
    fake = Mock()
    fake.sock.getpeercert.return_value = b"real hikvision certificate"
    monkeypatch.setattr("hikcentral.http.client.HTTPSConnection", lambda *a, **k: fake)
    with pytest.raises(HikError, match="CERTIFICADO DO HIKCENTRAL MUDOU"):
        pinned_https_post("127.0.0.1", 443, "/artemis/example", b"face",
                          {"x-ca-key": "secret"}, "0" * 64)
    fake.connect.assert_called_once()
    fake.request.assert_not_called()
    fake.close.assert_called_once()


def test_cert_pin_approves_exact_sha256(monkeypatch):
    from unittest.mock import Mock
    from hikcentral import pinned_https_post
    certificate = b"cert from locally verified HikCentral"
    fingerprint = hashlib.sha256(certificate).hexdigest()
    fake = Mock()
    fake.sock.getpeercert.return_value = certificate
    fake.getresponse.return_value.status = 200
    fake.getresponse.return_value.read.return_value = b'{"code":"0"}'
    monkeypatch.setattr("hikcentral.http.client.HTTPSConnection", lambda *a, **k: fake)
    status, data = pinned_https_post("127.0.0.1", 443, "/artemis/example", b"{}",
                                     {"x-ca-key": "ak"}, fingerprint)
    assert status == 200
    assert data == b'{"code":"0"}'
    fake.connect.assert_called_once()
    fake.request.assert_called_once()
    fake.close.assert_called_once()


def test_pin_must_be_full_sha256():
    from hikcentral import normalize_pin
    with pytest.raises(HikError):
        normalize_pin("abc")
    assert normalize_pin("AB:" * 31 + "AB") == "AB" * 32


def test_configured_pin_uses_pinned_transport(monkeypatch):
    from hikcentral import HikCentral
    calls = []
    def fake_pinned(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, b'{"code": "0", "data": {"ok":true}}'
    monkeypatch.setattr("hikcentral.pinned_https_post", fake_pinned)
    api = HikCentral("127.0.0.1", 443, "ak", "sk", pinned_sha256="A" * 64)
    assert api.post("/artemis/api/acs/v1/privilege/group", {"type": 2})["data"]["ok"]
    assert calls[0][0][0:2] == ("127.0.0.1", 443)
    assert calls[0][0][5] == "A" * 64

def test_registration_and_appointment_transmit_identical_local_checkout():
    import json
    api, session = client([
        {"code": "0", "data": {"appointRecordId": "appointment", "visitorId": "visitor"}},
        {"code": "0", "data": {"appointRecordId": "registration"}},
    ])
    levels = [{"id": "1", "name": "Day use", "elements": [{"Element": {"ID": "a"}}]}]
    info = {"visitStartTime": "2099-10-31T18:00:00-03:00",
            "visitEndTime": "2099-11-01T23:59:59-03:00",
            "visitorInfoList": [{"VisitorInfo": {"visitorGivenName": "Teste", "visitorFamilyName": "UID"}}]}
    appoint, visitor = api.create_appointment(info, levels)
    api.register_visitor(info, levels, appoint, visitor)
    appointment_payload = json.loads(session.calls[0][1]["data"])
    checkin_payload = json.loads(session.calls[1][1]["data"])
    assert appointment_payload["appointEndTime"] == "2099-11-01T23:59:59-03:00"
    assert checkin_payload["visitEndTime"] == appointment_payload["appointEndTime"]


def test_find_registration_unique_readonly_and_no_creation():
    api, session = client({"code": "0", "data": {"list": [
        {"recordId": "REC01", "visitorBaseInfo": {
            "visitorId": "vis-1", "visitStartTime": "2099-10-31T12:00:00-03:00",
            "visitEndTime": "2099-11-01T23:59:59-03:00"}},
        {"recordId": "OTHER", "visitorBaseInfo": {
            "visitorId": "vis-2", "visitStartTime": "2099-10-31T12:00:00-03:00",
            "visitEndTime": "2099-11-01T23:59:59-03:00"}},
    ], "totalNum": 2}})
    found = api.find_existing_registration("vis-1", "2099-10-31T12:00:00-03:00",
                                           "2099-11-01T23:59:59-03:00")
    assert found == "REC01"
    assert len(session.calls) == 1
    assert session.calls[0][0].endswith("/artemis/api/visitor/v1/register/getVistorRegisterRecord")


def test_find_registration_missing_stays_unresolved():
    api, session = client({"code": "0", "data": {"list": [], "totalNum": 0}})
    assert api.find_existing_registration("v1", "2099-10-31T12:00:00-03:00",
                                          "2099-11-01T23:59:59-03:00") is None
