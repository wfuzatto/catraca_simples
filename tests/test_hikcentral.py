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
        self.last = None
    def post(self, url, **kwargs):
        self.last = (url, kwargs)
        return Response(self.data)


def test_signer():
    path, key, secret, timestamp = "/artemis/api/visitor/v1/registerment", "ak", "sk", "123"
    canonical = "POST\n*/*\napplication/json\nx-ca-key:ak\nx-ca-timestamp:123\n" + path
    expected = base64.b64encode(hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).digest()).decode()
    assert sign(path, key, secret, timestamp) == expected


def test_groups():
    session = MockSession({"code": "0", "data": {"VisitorGroupList": {"VisitorGroup": [
        {"indexCode": "1", "baseInfo": {"name": "Day use"}},
        {"indexCode": "2", "baseInfo": {"name": "Hóspedes Vale"}}
    ]}}})
    client = HikCentral("192.168.100.102", 37443, "ak", "sk", session=session)
    assert client.visitor_groups() == ["Day use", "Hóspedes Vale"]
    assert session.last[0].endswith("/artemis/api/visitor/v1/visitorgroups")
    assert session.last[1]["verify"] is True


def test_no_id_is_uncertain():
    client = HikCentral("192.168.100.102", 37443, "ak", "sk", session=MockSession({"code": 0, "data": {}}))
    with pytest.raises(HikError):
        client.create_visitor({})


def test_name_normalization():
    assert normalize("HÓSPEDES  VALE") == normalize("hospedes vale")
