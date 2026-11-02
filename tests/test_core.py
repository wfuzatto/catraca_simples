from datetime import datetime
from io import BytesIO
import pytest
from PIL import Image
from core import (TZ, Visitor, default_settings, end_for_ticket, parse_ticket_lines,
                  parse_access_lines, registration_payload, save_settings, load_settings,
                  save_pending, load_pending, mark_assigned, mark_registered, clear_pending,
                  save_last_delivery, load_last_delivery, prepare_face_jpeg,
                  save_api_keys, load_api_keys, validate_visitor, GROUPS, ACCESS_LEVELS)
from devices import to_card_number


def jpeg():
    buffer = BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_day_pass_lengths():
    start = datetime(2026, 10, 24, 10, tzinfo=TZ)
    tickets = default_settings()["tickets"]
    assert [end_for_ticket(start, tickets[x]).day for x in ["1 dia", "2 dias", "3 dias"]] == [24, 25, 26]
    assert end_for_ticket(start, tickets["1 dia"]).strftime("%H:%M:%S") == "17:00:00"
    assert tickets["3 dias"]["checkin"] == "09:00"


def test_payload_visitor_group_photo_card_remark():
    start = datetime(2099, 10, 24, 10, tzinfo=TZ)
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


def test_registration_payload_accepts_face_only_card_only_or_both():
    start = datetime(2099, 10, 24, 10, tzinfo=TZ)
    end = end_for_ticket(start, default_settings()["tickets"]["1 dia"])

    face = registration_payload(Visitor("Joao", "Silva", "Day use", jpeg(), "", start, end))
    face_info = face["visitorInfoList"][0]["VisitorInfo"]
    assert "faces" in face_info and "cards" not in face_info

    card = registration_payload(Visitor("Joao", "Silva", "Day use", b"", "CARD123", start, end))
    card_info = card["visitorInfoList"][0]["VisitorInfo"]
    assert "cards" in card_info and "faces" not in card_info

    both = registration_payload(Visitor("Joao", "Silva", "Day use", jpeg(), "CARD123", start, end))
    both_info = both["visitorInfoList"][0]["VisitorInfo"]
    assert "faces" in both_info and "cards" in both_info


def test_registration_requires_at_least_one_credential():
    start = datetime(2099, 10, 24, 10, tzinfo=TZ)
    end = end_for_ticket(start, default_settings()["tickets"]["1 dia"])
    visitor = Visitor("Joao", "Silva", "Day use", b"", "", start, end)
    with pytest.raises(ValueError, match="facial"):
        registration_payload(visitor)


def test_settings_and_pending(tmp_path):
    s = default_settings()
    save_settings(s, tmp_path)
    assert load_settings(tmp_path)["host"] == s["host"]
    save_pending("v001", ["a", "b"], tmp_path, expect_face=False, expect_card=True)
    assert load_pending(tmp_path)["appointment_id"] == ""
    assert load_pending(tmp_path)["expect_face"] is False
    assert load_pending(tmp_path)["expect_card"] is True
    with pytest.raises(RuntimeError, match="pendente"):
        save_pending("v002", ["c"], tmp_path)
    mark_registered("checkin-001", tmp_path)
    assert load_pending(tmp_path)["registration_record_id"] == "checkin-001"
    mark_assigned("a", tmp_path)
    assert load_pending(tmp_path)["assigned"] == ["a"]
    clear_pending(tmp_path)
    assert load_pending(tmp_path) is None
    save_last_delivery("v001", ["door-1", "door-2", "door-1"], tmp_path,
                       expect_face=True, expect_card=False)
    last = load_last_delivery(tmp_path)
    assert last["visitor_id"] == "v001"
    assert last["door_ids"] == ["door-1", "door-2"]
    assert last["expect_face"] is True and last["expect_card"] is False
    assert last["entry_at"] == "" and last["registration_record_id"] == ""


def test_config_editing_and_uid_formats():
    assert parse_ticket_lines("Especial;5;09:00;20:00")["Especial"]["days"] == 5
    assert parse_access_lines("Day use=CATRACAS SABIA,SAIDA ACQUAVALE")["Day use"] == ["CATRACAS SABIA", "SAIDA ACQUAVALE"]
    assert parse_access_lines("Day use=\nVisitors=ENTRADA ACQUAVALE")["Day use"] == []
    uid = bytes.fromhex("A1B2C3D4")
    assert to_card_number(uid, "HEX") == "A1B2C3D4"
    assert to_card_number(uid, "DECIMAL_BIG") == str(0xA1B2C3D4)
    assert to_card_number(uid, "DECIMAL_LITTLE") == str(0xD4C3B2A1)


def test_defaults_do_not_grant_every_access_level():
    settings = default_settings()
    assert (settings["host"], settings["port"]) == ("127.0.0.1", 443)
    assert all(not levels for levels in settings["group_access"].values())


def test_migrate_legacy_all_levels_default_to_unassigned(tmp_path):
    settings = default_settings()
    settings["group_access"] = {group: ACCESS_LEVELS.copy() for group in GROUPS}
    save_settings(settings, tmp_path)
    loaded = load_settings(tmp_path)
    assert loaded["group_access"] == {group: [] for group in GROUPS}


def test_ticket_validation_and_duplicate_names():
    assert parse_ticket_lines("Especial;1;02:00;02:30")["Especial"]["checkin"] == "02:00"
    with pytest.raises(ValueError, match="duplicado"):
        parse_ticket_lines("Dia;1;09:00;17:00\nDia;2;09:00;17:00")
    with pytest.raises(ValueError, match="HH:MM"):
        parse_ticket_lines("Invalido;1;09:00;25:00")
    with pytest.raises(ValueError, match="anterior"):
        parse_ticket_lines("Invalido;1;18:00;09:00")


def test_visitor_expiry_must_be_future():
    now = datetime(2099, 10, 24, 10, tzinfo=TZ)
    photo = jpeg()
    valid = Visitor("João", "Silva", "Day use", photo, "1234",
                    now, now.replace(hour=11))
    validate_visitor(valid, now=now)
    expired = Visitor("João", "Silva", "Day use", photo, "1234",
                      now.replace(hour=8), now.replace(hour=9))
    with pytest.raises(ValueError, match="expirou"):
        validate_visitor(expired, now=now)


def test_prepare_photo_normalizes_png_to_small_jpeg():
    source = BytesIO()
    Image.new("RGB", (2200, 1200), "white").save(source, format="PNG")
    result = prepare_face_jpeg(source.getvalue())
    with Image.open(BytesIO(result)) as image:
        assert image.format == "JPEG"
        assert image.size == (1024, 559)
    assert len(result) <= 2_000_000
    with pytest.raises(ValueError, match="imagem"):
        prepare_face_jpeg(b"not an image")


def test_prepare_photo_corrects_exif_orientation():
    source = BytesIO()
    image = Image.new("RGB", (80, 40), "white")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, format="JPEG", exif=exif)
    result = prepare_face_jpeg(source.getvalue())
    with Image.open(BytesIO(result)) as output:
        assert output.size == (40, 80)


def test_windows_dpapi_round_trip(tmp_path):
    import os
    if os.name != "nt":
        pytest.skip("DPAPI só existe no Windows")
    save_api_keys("test-app-key", "test-app-secret", tmp_path)
    encrypted = (tmp_path / "secrets.dpapi").read_bytes()
    assert b"test-app-secret" not in encrypted
    assert load_api_keys(tmp_path) == ("test-app-key", "test-app-secret")

def test_checkout_rollover_month_and_year_and_payload_timezone():
    from core import parse_local
    from datetime import datetime
    cases = [
        ("31/10/2099 10:45", "2 dias", "01/11/2099 17:00"),
        ("31/12/2099 11:00", "3 dias", "02/01/2100 17:00"),
        ("24/10/2099 10:45", "1 dia", "24/10/2099 17:00"),
    ]
    for start_text, ticket_name, expected in cases:
        start = parse_local(start_text)
        end = end_for_ticket(start, default_settings()["tickets"][ticket_name])
        assert end.strftime("%d/%m/%Y %H:%M") == expected
        visitor = Visitor("Teste", "Sobrenome", "Day use", b"", "12345", start, end)
        payload = registration_payload(visitor)
        sent_end = datetime.fromisoformat(payload["visitEndTime"])
        assert sent_end.astimezone(TZ).date() == end.date()
        assert sent_end.hour == 17 and sent_end.minute == 0 and sent_end.second == 0
        assert payload["visitEndTime"].endswith("-03:00")


def test_pending_auto_recovery_has_date_interval(tmp_path):
    save_pending("v001", ["level"], tmp_path, appointment_id="appointment1",
                 door_ids=["door1"], registered=False, expect_face=False, expect_card=True,
                 entry_at="2099-10-31T12:00:00-03:00",
                 exit_at="2099-11-01T23:59:59-03:00")
    pending = load_pending(tmp_path)
    assert pending["registered"] is False
    assert pending["entry_at"].startswith("2099-10-31")
    assert pending["exit_at"].startswith("2099-11-01")
    mark_registered("record-abc", tmp_path)
    assert load_pending(tmp_path)["registered"] is True
    clear_pending(tmp_path)


def test_preferred_group_is_per_workstation_and_survives_restart(tmp_path):
    from core import initial_visitor_group
    point_a = tmp_path / "caixa_1"
    point_b = tmp_path / "caixa_2"
    conf_a = default_settings()
    conf_b = default_settings()
    assert conf_a["preferred_group"] == ""
    conf_a["preferred_group"] = "Hóspedes Vale"
    conf_b["preferred_group"] = "Day use"
    save_settings(conf_a, point_a)
    save_settings(conf_b, point_b)
    assert load_settings(point_a)["preferred_group"] == "Hóspedes Vale"
    assert load_settings(point_b)["preferred_group"] == "Day use"
    real_groups = ["Day use", "Hóspedes Serra", "Hóspedes Vale"]
    assert initial_visitor_group(real_groups, load_settings(point_a)["preferred_group"]) == "Hóspedes Vale"
    assert initial_visitor_group(real_groups, load_settings(point_b)["preferred_group"]) == "Day use"
    assert initial_visitor_group(real_groups, "Outro grupo") == "Day use"
    assert initial_visitor_group(real_groups, "") == "Day use"
    assert initial_visitor_group([], "Hóspedes Vale") == ""


def test_group_lock_validation_after_sync():
    import pytest
    from core import validate_visitor_group_selection
    groups = ["Day use", "Hóspedes Vale", "Hóspedes Serra"]
    assert validate_visitor_group_selection("Day use", groups) == "Day use"
    assert validate_visitor_group_selection("Hóspedes Vale", groups, "Hóspedes Vale") == "Hóspedes Vale"
    with pytest.raises(ValueError, match="bloqueado"):
        validate_visitor_group_selection("Day use", groups, "Hóspedes Vale")
    with pytest.raises(ValueError, match="não existe"):
        validate_visitor_group_selection("Day use", groups, "Grupo desativado")
    with pytest.raises(ValueError, match="não confirmado"):
        validate_visitor_group_selection("Visitors", groups)


def test_window_geometry_respects_display_and_taskbar():
    from responsive_ui import initial_geometry
    for screen_width, screen_height in ((1920, 1080), (1366, 768), (1280, 720), (1024, 768), (800, 600)):
        width, height, x, y = initial_geometry(screen_width, screen_height)
        assert 0 <= x < screen_width
        assert 0 <= y < screen_height
        assert width + x <= screen_width
        assert height + y <= screen_height
        assert width < screen_width
        assert height < screen_height



def test_ticket_daily_windows_start_end_and_migration(tmp_path):
    from core import (normalize_ticket_start, validate_ticket_period,
                      ticket_hours, parse_local)
    from datetime import timedelta
    ticket = parse_ticket_lines("3 dias;3;09:00;17:00")["3 dias"]
    assert ticket_hours(ticket) == ("09:00", "17:00")
    early = parse_local("01/11/2099 00:14")
    first = normalize_ticket_start(early, ticket)
    last = end_for_ticket(first, ticket)
    assert first.strftime("%d/%m/%Y %H:%M") == "01/11/2099 09:00"
    assert last.strftime("%d/%m/%Y %H:%M") == "03/11/2099 17:00"
    validate_ticket_period(first, last, ticket)
    with pytest.raises(ValueError, match="Entrada"):
        validate_ticket_period(first.replace(hour=8), last, ticket)
    with pytest.raises(ValueError, match="Saída"):
        validate_ticket_period(first, last + timedelta(minutes=5), ticket)
    next_opening = normalize_ticket_start(parse_local("01/11/2099 20:00"), ticket)
    assert next_opening.strftime("%d/%m/%Y %H:%M") == "02/11/2099 09:00"
    assert parse_ticket_lines("Antigo;2;23:59")["Antigo"] == {
        "days": 2, "checkin": "09:00", "checkout": "23:59"
    }
    old = default_settings()
    old["tickets"] = {"Antigo": {"days": 2, "checkout": "23:59"}}
    save_settings(old, tmp_path)
    assert load_settings(tmp_path)["tickets"]["Antigo"]["checkin"] == "09:00"
    assert load_settings(tmp_path)["tickets"]["Antigo"]["checkout"] == "23:59"


def test_daily_schedule_requires_explicit_admin_confirmation():
    from core import parse_schedule_lines, require_confirmed_daily_schedule
    ticket = parse_ticket_lines("3 dias;3;09:00;17:00")["3 dias"]
    levels = ["ENTRADA ACQUAVALE", "CATRACAS SABIA"]
    with pytest.raises(ValueError, match="NÃO confirmados"):
        require_confirmed_daily_schedule(levels, ticket, {})
    mapping = parse_schedule_lines(
        "ENTRADA ACQUAVALE=09:00-17:00\nCATRACAS SABIA=09:00-17:00")
    assert mapping == dict.fromkeys(levels, "09:00-17:00")
    require_confirmed_daily_schedule(levels, ticket, mapping)
    with pytest.raises(ValueError, match="NÃO confirmados"):
        require_confirmed_daily_schedule(levels, ticket, {"ENTRADA ACQUAVALE": "08:00-22:00"})


def test_face_preview_fills_frame_for_small_and_wide_images():
    from core import face_cover_preview
    for dimensions in [(25, 15), (640, 960), (2000, 200)]:
        source = BytesIO()
        Image.new("RGB", dimensions, (120, 80, 40)).save(source, format="JPEG")
        shown = face_cover_preview(source.getvalue(), 148, 133)
        assert shown.size == (148, 133)
        assert shown.getpixel((0, 0)) == shown.getpixel((147, 132))


def test_group_specific_visitor_access_mapping_cannot_use_unapproved_levels():
    from core import visitor_levels_for_group, verify_visitor_access_plan
    groups = ["Day use", "Hóspedes Vale", "Hóspedes Serra"]
    mapping = {
        "Day use": ["VISITANTE"],
        "Hóspedes Vale": ["NIVEL HOSPEDES"],
        "Hóspedes Serra": [],
    }
    assert visitor_levels_for_group("Day use", groups, mapping) == ["VISITANTE"]
    assert visitor_levels_for_group("Hóspedes Vale", groups, mapping) == ["NIVEL HOSPEDES"]
    with pytest.raises(ValueError, match="exatamente um"):
        visitor_levels_for_group("Hóspedes Serra", groups, mapping)
    with pytest.raises(ValueError, match="exatamente um"):
        visitor_levels_for_group("Hóspedes Vale", groups, {"Hóspedes Vale": ["VISITANTE", "All-Day"]})
    with pytest.raises(ValueError, match="confirmado"):
        visitor_levels_for_group("Visitors", groups, mapping)

    ten_doors = [f"door-{i}" for i in range(10)]
    verify_visitor_access_plan([{"id": "LV001", "name": "VISITANTE"}], ten_doors, ["VISITANTE"])
    verify_visitor_access_plan([{"id": "LV002", "name": "NIVEL HOSPEDES"}],
                               ten_doors[:3], ["NIVEL HOSPEDES"])
    with pytest.raises(ValueError, match="dez|10"):
        verify_visitor_access_plan([{"id": "LV001", "name": "VISITANTE"}],
                                   ten_doors[:9], ["VISITANTE"])
    with pytest.raises(ValueError, match="exatamente um"):
        verify_visitor_access_plan([{"id": "LV001", "name": "VISITANTE"},
                                    {"id": "LV002", "name": "NIVEL HOSPEDES"}],
                                   ten_doors, ["VISITANTE", "NIVEL HOSPEDES"])
    with pytest.raises(ValueError, match="pontos"):
        verify_visitor_access_plan([{"id": "LV002", "name": "NIVEL HOSPEDES"}],
                                   ["door1", "door1"], ["NIVEL HOSPEDES"])


def test_appointment_inflight_marker_blocks_retries_and_upgrades(tmp_path):
    from core import mark_appointment_inflight, load_pending
    mark_appointment_inflight(tmp_path)
    marker = load_pending(tmp_path)
    assert marker["stage"] == "appointment_inflight"
    assert not marker["visitor_id"]
    assert not marker["appointment_id"]
    content = (tmp_path / "pending.json").read_text(encoding="utf-8")
    assert "faceData" not in content
    assert "cardNo" not in content
    with pytest.raises(RuntimeError, match="pendente"):
        mark_appointment_inflight(tmp_path)
    save_pending("VISITOR-1", ["VISITOR-LEVEL-ID"], tmp_path,
                 appointment_id="APPOINTMENT-1",
                 door_ids=[f"door-{i}" for i in range(10)],
                 registered=False, expect_face=False, expect_card=True,
                 entry_at="2026-11-02T09:00:00-03:00",
                 exit_at="2026-11-04T17:00:00-03:00")
    assert load_pending(tmp_path)["stage"] == "appointment_confirmed"
    with pytest.raises(RuntimeError, match="pendente"):
        save_pending("VISITOR-2", ["VISITOR-LEVEL-ID"], tmp_path)


def test_visitor_timezone_and_daily_window():
    from core import parse_local, registration_payload, validate_ticket_period
    start = parse_local("02/11/2026 09:00")
    end = parse_local("04/11/2026 17:00")
    ticket = {"days": 3, "checkin": "09:00", "checkout": "17:00"}
    validate_ticket_period(start, end, ticket)
    payload = registration_payload(
        Visitor("Teste", "Local", "Hóspedes Serra", b"", "123456789", start, end, "123"))
    assert payload["visitStartTime"] == "2026-11-02T09:00:00-03:00"
    assert payload["visitEndTime"] == "2026-11-04T17:00:00-03:00"
    info = payload["visitorInfoList"][0]["VisitorInfo"]
    assert info["remark"] == "123"
    assert info["cards"][0]["cardNo"] == "123456789"
