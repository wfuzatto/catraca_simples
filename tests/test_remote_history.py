"""Consultas remotas simuladas: não exigem servidor, webcam nem cartão real."""
import base64
import io
from datetime import datetime
from PIL import Image
import pytest
from remote_history import (
    CHECKINS_PATH, PHOTO_PATH, DETAIL_PATH, search_window,
    fetch_checkins, fetch_visitor_photo, normalize_checkin,
    decode_photo_response, visitor_details,
)
from hikcentral import HikError


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.calls = []
    def post(self, path, payload, **options):
        self.calls.append((path, payload, options))
        return self.data


def example_record(visitor="812", record="40", name="Maria Alves"):
    return {
        "recordId": record,
        "visitorStatus": "0",
        "visitorBaseInfo": {
            "visitorId": visitor, "fullName": name,
            "visitStartTime": "2026-11-02T12:57:00-03:00",
            "visitEndTime": "2026-11-04T17:00:00-03:00",
            "visitorGroup": "Hóspedes Vale", "remark": "Apto 18",
            "smallPhotoUrl": "opaque-photo-id",
        },
    }


def test_date_filter_includes_last_day_and_rejects_long_queries():
    start, end = search_window("01/11/2026", "02/11/2026")
    assert start == "2026-11-01T00:00:00-03:00"
    assert end == "2026-11-03T00:00:00-03:00"
    with pytest.raises(ValueError, match="32 dias"):
        search_window("01/01/2026", "02/03/2026")
    with pytest.raises(ValueError):
        search_window("02/11/2026", "01/11/2026")


def test_paginated_checkin_allows_multiple_visits_for_same_visitor():
    records = [example_record(record="40"), example_record(record="41")]
    client = FakeClient({"code": "0", "data": {
        "list": records, "totalNum": 250,
    }})
    result = fetch_checkins(
        client, "2026-11-01T00:00:00-03:00", "2026-11-03T00:00:00-03:00",
        page=1, name="Maria")
    assert len(result["rows"]) == 2 and result["has_next"]
    assert all(r["door_ids"] == [] and r["access_names"] == [] for r in result["rows"])
    assert result["rows"][0]["group"] == "Hóspedes Vale"
    path, request, opts = client.calls[0]
    assert path == CHECKINS_PATH and request["orderType"] == "1"
    assert request["visitorName"] == "Maria" and not opts


def test_never_guess_missing_identifiers_or_privileges():
    assert normalize_checkin({"visitorBaseInfo": {}}) is None
    item = normalize_checkin(example_record())
    assert item["visitor_id"] == "812"
    assert item["registration_record_id"] == "40"
    assert item["photo_url"] == "opaque-photo-id"
    assert not item["access_names"] and not item["door_ids"]


def test_invalid_server_response_is_not_silently_treated_as_empty():
    client = FakeClient({"data": {"error": "not a list"}})
    with pytest.raises(HikError, match="lista paginada"):
        fetch_checkins(client, "2026-11-01T00:00:00-03:00",
                       "2026-11-03T00:00:00-03:00")


def test_detail_lookup_is_read_only():
    client = FakeClient({"data": {"visitorId": "812", "visitorGroupName": "Hóspedes Vale"}})
    assert visitor_details(client, "812")["visitorId"] == "812"
    assert client.calls[0][0] == DETAIL_PATH


def test_photo_download_passes_opaque_reference_only_to_artemis():
    output = io.BytesIO()
    Image.new("RGB", (40, 30)).save(output, "JPEG")
    data_uri = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    client = FakeClient({"code": "0", "data": data_uri})
    picture = fetch_visitor_photo(client, "812", "photo-ref")
    assert picture[:3] == b"\xff\xd8\xff"
    path, payload, options = client.calls[0]
    assert path == PHOTO_PATH
    assert payload == {"visitorId": "812", "picType": "0", "picUrl": "photo-ref"}
    assert options == {"allow_image_data": True}


def test_malicious_picture_and_bad_content_rejected():
    with pytest.raises(HikError):
        decode_photo_response("data:text/html;base64,SGVsbG8=")
    with pytest.raises(HikError):
        decode_photo_response("data:image/jpeg;base64," + base64.b64encode(b"not a jpeg").decode())
    with pytest.raises(HikError):
        fetch_visitor_photo(FakeClient({}), "812", "x"*260)


def test_remote_history_has_no_automatic_write_endpoints():
    import inspect
    import remote_history
    source = inspect.getsource(remote_history)
    assert "registerment/update" not in source
    assert "auth/reapplication" not in source
    assert "single/addPersons" not in source
