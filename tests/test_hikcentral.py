"""Testes com respostas simuladas: sem chamadas ao HikCentral real."""
import base64
import hashlib
import hmac
import pytest
from hikcentral import HikCentral, HikError, normalize, sign


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class MockSession:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        data = self.data.pop(0) if isinstance(self.data, list) else self.data
        return Response(data)


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


def test_normalization():
    assert normalize("HÓSPEDES  VALE") == normalize("hospedes vale")
