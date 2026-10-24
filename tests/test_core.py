from datetime import datetime
from io import BytesIO
from PIL import Image
from core import (TZ, Visitor, default_settings, end_for_ticket, parse_ticket_lines,
                  parse_access_lines, registration_payload, save_settings, load_settings,
                  save_pending, load_pending, mark_assigned, clear_pending)
from devices import to_card_number


def jpeg():
    buffer = BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_day_pass_lengths():
    start = datetime(2026, 10, 24, 10, tzinfo=TZ)
    tickets = default_settings()["tickets"]
    assert [end_for_ticket(start, tickets[x]).day for x in ["1 dia", "2 dias", "3 dias"]] == [24, 25, 26]
    assert end_for_ticket(start, tickets["1 dia"]).strftime("%H:%M:%S") == "23:59:59"


def test_payload_visitor_group_photo_card_remark():
    start = datetime(2026, 10, 24, 10, tzinfo=TZ)
    end = end_for_ticket(start, default_settings()["tickets"]["2 dias"])
    v = Visitor("João", "Silva", "Hóspedes Serra", jpeg(), "12345678", start, end, "RES00001")
    payload = registration_payload(v)
    body = payload["visitorInfoList"][0]["VisitorInfo"]
    assert body["visitorGivenName"] == "João"
    assert body["visitorGroupName"] == "Hóspedes Serra"
    assert body["remark"] == "RES00001"
    assert body["cards"][0]["cardNo"] == "12345678"
    assert len(body["faces"][0]["faceData"]) > 10
    assert payload["visitEndTime"].endswith("-03:00")


def test_settings_and_pending(tmp_path):
    s = default_settings()
    save_settings(s, tmp_path)
    assert load_settings(tmp_path)["host"] == s["host"]
    save_pending("v001", ["a", "b"], tmp_path)
    mark_assigned("a", tmp_path)
    assert load_pending(tmp_path)["assigned"] == ["a"]
    clear_pending(tmp_path)
    assert load_pending(tmp_path) is None


def test_config_editing_and_uid_formats():
    assert parse_ticket_lines("Especial;5;20:00")["Especial"]["days"] == 5
    assert parse_access_lines("Day use=CATRACAS SABIA,SAIDA ACQUAVALE")["Day use"] == ["CATRACAS SABIA", "SAIDA ACQUAVALE"]
    uid = bytes.fromhex("A1B2C3D4")
    assert to_card_number(uid, "HEX") == "A1B2C3D4"
    assert to_card_number(uid, "DECIMAL_BIG") == str(0xA1B2C3D4)
    assert to_card_number(uid, "DECIMAL_LITTLE") == str(0xD4C3B2A1)
