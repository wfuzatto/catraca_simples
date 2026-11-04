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


def test_real_visitor_api_paths_include_only_visitor_level_and_local_validity():
    import json
    from core import verify_visitor_access_plan, registration_payload, Visitor, parse_local
    api, session = client([
        {"code": "0", "data": {"appointRecordId": "appointment-1", "visitorId": "visitor-1"}},
        {"code": "0", "data": {"recordId": "registration-1"}},
        {"code": "0", "data": {}},
        {"code": "0", "data": {}},
    ])
    selected = [{"id": "17", "name": "VISITANTE",
                 "elements": [{"Element": {"ID": f"point-{n}"}} for n in range(10)]}]
    doors = [f"point-{n}" for n in range(10)]
    verify_visitor_access_plan(selected, doors, ["VISITANTE"])
    start = parse_local("02/11/2026 09:00")
    end = parse_local("04/11/2026 17:00")
    info = registration_payload(
        Visitor("Visitante", "Teste", "Hóspedes Serra", b"", "1234", start, end, "A123"))
    appointment, visitor_id = api.create_appointment(info, selected)
    registration = api.register_visitor(info, selected, appointment, visitor_id)
    assert registration == "registration-1"
    api.assign_access(visitor_id, selected[0]["id"])
    api.apply(visitor_id, doors)
    paths = [url.split(":443")[-1] for url, _ in session.calls]
    assert paths == [
        "/artemis/api/visitor/v2/appointment",
        "/artemis/api/visitor/v1/registerment",
        "/artemis/api/acs/v1/privilege/group/single/addPersons",
        "/artemis/api/visitor/v1/auth/reapplication",
    ]
    assert all("/resource/v1/person/single/add" not in path for path in paths)
    reserve = json.loads(session.calls[0][1]["data"])
    checkin = json.loads(session.calls[1][1]["data"])
    assignment = json.loads(session.calls[2][1]["data"])
    for request, start_key, end_key in [
        (reserve, "appointStartTime", "appointEndTime"),
        (checkin, "visitStartTime", "visitEndTime")
    ]:
        assert request[start_key] == "2026-11-02T09:00:00-03:00"
        assert request[end_key] == "2026-11-04T17:00:00-03:00"
        level = request["accessInfo"]["accessLevelList"]
        assert len(level) == 1
        assert level[0]["accessLevel"]["id"] == 17
        assert level[0]["accessLevel"]["baseInfo"]["name"] == "VISITANTE"
        personal = request["visitorInfoList"][0]["VisitorInfo"]
        assert personal["visitorGroupName"] == "Hóspedes Serra"
        assert personal["cards"] == [{"cardNo": "1234"}]
        assert personal["remark"] == "A123"
    assert assignment["type"] == 2
    assert assignment["privilegeGroupId"] == "17"
    assert assignment["list"] == [{"id": "visitor-1"}]
    assert json.loads(session.calls[3][1]["data"])["doorIndexCodes"] == ",".join(doors)


def test_untrusted_error_messages_do_not_expose_credentials_or_card():
    from hikcentral import _http_failure
    secret = "super-secret-cannot-leak"
    raw = {"code": "500", "msg": f"unexpected error {secret} cardNo=12345678"}
    msg = _http_failure(500, raw, "/artemis/api/visitor/v1/registerment")
    assert secret not in msg
    assert "12345678" not in msg
    assert "500" in msg


@pytest.mark.parametrize("valid_times", [0, 1, 5, 100])
def test_valid_times_survives_both_visitor_appointment_and_checkin_payloads(valid_times):
    """Mock only: never sends a real visitor to HikCentral."""
    import json
    from core import Visitor, parse_local, registration_payload
    api, session = client([
        {"code": "0", "data": {"appointRecordId": "appointment-1", "visitorId": "visitor-1"}},
        {"code": "0", "data": {"recordId": "registration-1"}},
    ])
    info = registration_payload(Visitor(
        "Visitante", "Teste", "Day use", b"", "123456",
        parse_local("02/11/2099 09:00"), parse_local("02/11/2099 17:00")), valid_times)
    levels = [{"id": "10", "name": "VISITANTE"}]
    appointment, visitor = api.create_appointment(info, levels)
    api.register_visitor(info, levels, appointment, visitor)
    assert len(session.calls) == 2
    assert session.calls[0][0].endswith("/artemis/api/visitor/v2/appointment")
    assert session.calls[1][0].endswith("/artemis/api/visitor/v1/registerment")
    for _, request in session.calls:
        body = json.loads(request["data"])
        sent = body["visitorInfoList"][0]["VisitorInfo"]
        assert sent["accessInfo"]["qrCodeValidNum"] == 0  # Sem limite no HikCentral
        assert sent["accessInfo"]["electrostaticDetectionType"] == 0
        assert sent["cards"][0]["cardNo"] == "123456"
    assert json.loads(session.calls[0][1]["data"])["appointStartTime"] == "2099-11-02T09:00:00-03:00"
    assert json.loads(session.calls[1][1]["data"])["visitEndTime"] == "2099-11-02T17:00:00-03:00"


def test_edit_existing_visitor_uses_update_endpoint_and_original_id():
    import json
    api, session = client({"code": "0", "data": {"appointRecordId": "updated-record"}})
    info = {
        "visitStartTime": "2026-11-02T09:00:00-03:00",
        "visitEndTime": "2026-11-02T17:00:00-03:00",
        "visitorInfoList": [{"VisitorInfo": {
            "visitorGivenName": "Ana", "visitorFamilyName": "Teste",
            "cards": [{"cardNo": "123456"}]
        }}],
    }
    result = api.update_registered_visitor("original-record", "existing-visitor", info, [{"id":"g1","name":"DAY_USE"}])
    assert result == "updated-record"
    assert len(session.calls) == 1
    path, request = session.calls[0]
    assert path.endswith("/artemis/api/visitor/v1/registerment/update")
    body = json.loads(request["data"])
    assert body["appointRecordId"] == "original-record"
    assert body["visitorInfoList"][0]["VisitorInfo"]["visitorId"] == "existing-visitor"


def test_edit_visitor_rejects_missing_credential_before_network():
    import pytest
    api, session = client([])
    info = {"visitStartTime":"2026-11-02T09:00:00-03:00",
            "visitEndTime":"2026-11-02T17:00:00-03:00",
            "visitorInfoList":[{"VisitorInfo":{"visitorGivenName":"Ana"}}]}
    with pytest.raises(Exception, match="credencial"):
        api.update_registered_visitor("record", "visitor", info, [{"id":"1","name":"DAY_USE"}])
    assert not session.calls


def _visitor_checkin_fixture():
    return {
        "visitStartTime": "2099-11-04T09:00:00-03:00",
        "visitEndTime": "2099-11-04T17:00:00-03:00",
        "visitorInfoList": [{"VisitorInfo": {
            "visitorGivenName": "Teste", "visitorFamilyName": "Offline",
            "visitorGroupName": "Day use", "cards": [{"cardNo": "1234"}],
        }}],
    }


def test_registerment_includes_documented_watchlist_without_mutating_reservation():
    import json
    api, session = client({"code": "0", "data": {"appointRecordId": "checkin-1"}})
    info = _visitor_checkin_fixture()
    api.register_visitor(info, [{"id": "23", "name": "DAY_USE"}], "appt-1", "visitor-1")
    payload = json.loads(session.calls[0][1]["data"])
    assert payload["appointId"] == "appt-1"
    assert payload["visitorId"] == "visitor-1"
    assert payload["visitorInfoList"][0]["VisitorInfo"]["watchListInfo"] == [
        {"enableRegister": 1, "ID": "0", "Type": "1"}]
    assert "watchListInfo" not in info["visitorInfoList"][0]["VisitorInfo"]


def test_registerment_131_reconciles_existing_checkin_without_duplicate_post():
    api, session = client([
        {"code": "131", "msg": "operation rejected"},
        {"code": "0", "data": {"list": [{
            "recordId": "checkin-confirmed", "visitorBaseInfo": {
                "visitorId": "visitor-1",
                "visitStartTime": "2099-11-04T09:00:00-03:00",
                "visitEndTime": "2099-11-04T17:00:00-03:00",
            },
        }], "totalNum": 1}},
    ])
    info = _visitor_checkin_fixture()
    result = api.register_visitor(info, [{"id": "23", "name": "DAY_USE"}], "appt-1", "visitor-1")
    assert result == "checkin-confirmed"
    assert len(session.calls) == 2
    assert session.calls[0][0].endswith("/visitor/v1/registerment")
    assert session.calls[1][0].endswith("/visitor/v1/register/getVistorRegisterRecord")


def test_registerment_131_without_confirmed_checkin_remains_blocked():
    api, session = client([
        {"code": "131", "msg": "validation rejected"},
        {"code": "0", "data": {"list": [], "totalNum": 0}},
    ])
    with pytest.raises(HikError, match="código 131"):
        api.register_visitor(
            _visitor_checkin_fixture(), [{"id": "23", "name": "DAY_USE"}],
            "appt-1", "visitor-1")
    assert len(session.calls) == 2
    assert all("appointment" not in url for url, _ in session.calls)


def test_registerment_success_without_optional_record_id_uses_readback():
    api, session = client([
        {"code": "0", "data": {"visitorId": "visitor-1"}},
        {"code": "0", "data": {"list": [{
            "recordId": "checkin-readback", "visitorBaseInfo": {
                "visitorId": "visitor-1",
                "visitStartTime": "2099-11-04T09:00:00-03:00",
                "visitEndTime": "2099-11-04T17:00:00-03:00",
            },
        }], "totalNum": 1}},
    ])
    assert api.register_visitor(
        _visitor_checkin_fixture(), [{"id": "23", "name": "DAY_USE"}],
        "appt-1", "visitor-1") == "checkin-readback"
    assert len(session.calls) == 2
