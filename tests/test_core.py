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
    assert body["certificateNo"] == ""
    assert body["certificateNo"] != body["cards"][0]["cardNo"]
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
    assert card_info["cards"] == [{"cardNo": "CARD123"}]
    assert card_info["certificateNo"] == ""

    both = registration_payload(Visitor("Joao", "Silva", "Day use", jpeg(), "CARD123", start, end))
    both_info = both["visitorInfoList"][0]["VisitorInfo"]
    assert "faces" in both_info and "cards" in both_info


def test_registration_requires_at_least_one_credential():
    start = datetime(2099, 10, 24, 10, tzinfo=TZ)
    end = end_for_ticket(start, default_settings()["tickets"]["1 dia"])
    visitor = Visitor("Joao", "Silva", "Day use", b"", "", start, end)
    with pytest.raises(ValueError, match="facial"):
        registration_payload(visitor)
    payload = registration_payload(visitor, editing=True, require_credential=False)
    info = payload["visitorInfoList"][0]["VisitorInfo"]
    assert "faces" not in info and "cards" not in info


def test_settings_and_pending(tmp_path):
    s = default_settings()
    assert s["auto_checkin"] is False
    s["auto_checkin"] = True
    save_settings(s, tmp_path)
    assert load_settings(tmp_path)["host"] == s["host"]
    assert load_settings(tmp_path)["auto_checkin"] is True
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


def test_current_visitor_defaults_are_scoped_to_real_approved_levels():
    settings = default_settings()
    assert (settings["host"], settings["port"]) == ("127.0.0.1", 443)
    assert settings["group_access"] == {
        "Day use": ["DAY_USE"],
        "Hóspedes Serra": ["VISITANTE_SERRA"],
        "Hóspedes Vale": ["VISITANTE_VALE"],
        "Visitors": [],
    }
    assert settings["schedule_windows"] == {
        "DAY_USE": "09:00-17:00",
        "VISITANTE_SERRA": "09:00-17:00",
        "VISITANTE_VALE": "09:00-17:00",
    }


def test_migrate_legacy_saved_levels_without_touching_other_settings(tmp_path):
    settings = default_settings()
    settings["access_mapping_revision"] = 0
    settings["group_access"] = {group: ACCESS_LEVELS.copy() for group in GROUPS}
    settings["group_access"]["Special event"] = ["CUSTOM_VISITOR_LEVEL"]
    settings["tickets"]["Especial"] = {"days": 1, "checkin": "10:00", "checkout": "16:00"}
    settings["valid_times"] = 7
    settings["preferred_group"] = "Hóspedes Vale"
    save_settings(settings, tmp_path)
    loaded = load_settings(tmp_path)
    assert loaded["group_access"]["Day use"] == ["DAY_USE"]
    assert loaded["group_access"]["Hóspedes Serra"] == ["VISITANTE_SERRA"]
    assert loaded["group_access"]["Hóspedes Vale"] == ["VISITANTE_VALE"]
    assert loaded["group_access"]["Special event"] == ["CUSTOM_VISITOR_LEVEL"]
    assert loaded["group_access"]["Visitors"] == []
    assert loaded["schedule_windows"]["DAY_USE"] == "09:00-17:00"
    assert loaded["access_mapping_revision"] == 1
    assert loaded["tickets"]["Especial"] == settings["tickets"]["Especial"]
    assert loaded["valid_times"] == 0
    assert loaded["preferred_group"] == "Hóspedes Vale"
    # Depois que a migração for salva, escolhas futuras não são sobrescritas.
    loaded["group_access"]["Day use"] = ["NOVO_VISITOR"]
    save_settings(loaded, tmp_path)
    assert load_settings(tmp_path)["group_access"]["Day use"] == ["NOVO_VISITOR"]
    # Uma cópia legada fica disponível para reversão da mudança de mapeamento.
    backup = tmp_path / "settings.backup-pre-visitor-levels.json"
    assert backup.is_file()
    assert "Hóspedes Serra" in backup.read_text(encoding="utf-8")
    import json
    legacy_data = json.loads(backup.read_text(encoding="utf-8"))
    assert legacy_data["group_access"]["Day use"] == ACCESS_LEVELS


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
                 checkin_requested=False,
                 entry_at="2099-10-31T12:00:00-03:00",
                 exit_at="2099-11-01T23:59:59-03:00")
    pending = load_pending(tmp_path)
    assert pending["registered"] is False
    assert pending["checkin_requested"] is False
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
    verify_visitor_access_plan([{"id": "LV001", "name": "DAY_USE"}], ten_doors[:8], ["DAY_USE"])
    verify_visitor_access_plan([{"id": "LV002", "name": "VISITANTE_SERRA"}],
                               ten_doors[:8], ["VISITANTE_SERRA"])
    verify_visitor_access_plan([{"id": "LV003", "name": "VISITANTE_VALE"}],
                               ten_doors[:3], ["VISITANTE_VALE"])
    verify_visitor_access_plan([{"id": "LV004", "name": "NIVEL HOSPEDES"}],
                               ten_doors[:3], ["NIVEL HOSPEDES"])
    for name, invalid in (("DAY_USE", 7), ("VISITANTE_SERRA", 9), ("VISITANTE_VALE", 8)):
        with pytest.raises(ValueError, match="prevê"):
            verify_visitor_access_plan([{"id": "L", "name": name}],
                                       ten_doors[:invalid], [name])
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
    start = parse_local("02/11/2099 09:00")
    end = parse_local("04/11/2099 17:00")
    ticket = {"days": 3, "checkin": "09:00", "checkout": "17:00"}
    validate_ticket_period(start, end, ticket)
    payload = registration_payload(
        Visitor("Teste", "Local", "Hóspedes Serra", b"", "123456789", start, end, "123"))
    assert payload["visitStartTime"] == "2099-11-02T09:00:00-03:00"
    assert payload["visitEndTime"] == "2099-11-04T17:00:00-03:00"
    info = payload["visitorInfoList"][0]["VisitorInfo"]
    assert info["remark"] == "123"
    assert info["cards"][0]["cardNo"] == "123456789"


@pytest.mark.parametrize("valid_times", [0, 1, 2, 5, 20, 100])
def test_valid_times_is_written_to_visitor_info_access(valid_times):
    from core import validate_valid_times
    start = datetime(2099, 11, 2, 9, tzinfo=TZ)
    end = datetime(2099, 11, 2, 17, tzinfo=TZ)
    obj = Visitor("Visitante", "Teste", "Day use", b"", "1234567", start, end)
    info = registration_payload(obj, valid_times)["visitorInfoList"][0]["VisitorInfo"]
    assert info["accessInfo"]["qrCodeValidNum"] == 0  # Nunca enviar um limite de visitas
    assert info["accessInfo"]["electrostaticDetectionType"] == 0
    assert validate_valid_times(valid_times) == valid_times


@pytest.mark.parametrize("invalid", [-1, 101, 1.5, "1", None, True, False])
def test_valid_times_rejects_outside_documented_range(invalid):
    from core import validate_valid_times
    start = datetime(2099, 11, 2, 9, tzinfo=TZ)
    end = datetime(2099, 11, 2, 17, tzinfo=TZ)
    obj = Visitor("Visitante", "Teste", "Day use", b"", "1234567", start, end)
    with pytest.raises(ValueError, match="VALID TIMES"):
        registration_payload(obj, invalid)
    with pytest.raises(ValueError, match="VALID TIMES"):
        validate_valid_times(invalid)


def test_valid_times_new_default_and_old_config_migrated_without_resetting_tickets(tmp_path):
    from core import validate_valid_times
    defaults = default_settings()
    assert defaults["valid_times"] == 0
    assert registration_payload(
        Visitor("Visitante", "Teste", "Day use", b"", "1234567",
                datetime(2099, 11, 2, 9, tzinfo=TZ), datetime(2099, 11, 2, 17, tzinfo=TZ))
    )["visitorInfoList"][0]["VisitorInfo"]["accessInfo"]["qrCodeValidNum"] == 0
    old = default_settings()
    old.pop("valid_times")
    save_settings(old, tmp_path)
    migrated = load_settings(tmp_path)
    assert migrated["valid_times"] == 0
    assert migrated["tickets"] == old["tickets"]
    migrated["valid_times"] = 7
    save_settings(migrated, tmp_path)
    assert load_settings(tmp_path)["valid_times"] == 0


def test_existing_nonzero_settings_migrate_and_survive_reopening(tmp_path):
    """Simula settings.json produzido pelo EXE antigo; demais preferências preservadas."""
    import json
    previous = default_settings()
    previous.update({
        "valid_times": 1, "host": "192.168.100.102", "camera_index": 2,
        "preferred_group": "Hóspedes Serra", "card_format": "DECIMAL_BIG",
    })
    previous["tickets"]["Especial"] = {"days": 4, "checkin": "09:00", "checkout": "17:00"}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(previous, ensure_ascii=False), encoding="utf-8")

    first_boot = load_settings(tmp_path)
    assert first_boot["valid_times"] == 0
    assert first_boot["host"] == previous["host"]
    assert first_boot["camera_index"] == previous["camera_index"]
    assert first_boot["preferred_group"] == previous["preferred_group"]
    assert first_boot["card_format"] == previous["card_format"]
    assert first_boot["tickets"]["Especial"] == previous["tickets"]["Especial"]
    assert json.loads(path.read_text(encoding="utf-8"))["valid_times"] == 0
    backup = tmp_path / "settings.backup-pre-valid-times-zero.json"
    assert json.loads(backup.read_text(encoding="utf-8"))["valid_times"] == 1

    second_boot = load_settings(tmp_path)
    assert second_boot == first_boot

    # Nem a gravação posterior de preferências pode reativar o limite.
    second_boot["valid_times"] = 100
    second_boot["camera_index"] = 4
    save_settings(second_boot, tmp_path)
    third_boot = load_settings(tmp_path)
    assert third_boot["valid_times"] == 0
    assert third_boot["camera_index"] == 4
    assert third_boot["tickets"]["Especial"] == previous["tickets"]["Especial"]


@pytest.mark.parametrize("group", ["Day use", "Hóspedes Serra", "Hóspedes Vale"])
def test_valid_times_zero_is_applied_to_all_visitor_groups(group):
    start = datetime(2099, 11, 2, 9, tzinfo=TZ)
    end = datetime(2099, 11, 2, 17, tzinfo=TZ)
    visit = Visitor("Teste", "Recepção", group, b"", "1234567", start, end)
    for old_value in (0, 1, 5, 100):
        info = registration_payload(visit, old_value)["visitorInfoList"][0]["VisitorInfo"]
        assert info["visitorGroupName"] == group
        assert info["accessInfo"]["qrCodeValidNum"] == 0


def test_calendar_validity_full_day_and_schedule_independent():
    from core import visitor_period_from_dates, require_confirmed_daily_schedule
    settings = default_settings()
    ticket = settings["tickets"]["2 dias"]
    start, end = visitor_period_from_dates("05/11/2099", "06/11/2099", settings, ticket)
    assert start.isoformat(timespec="seconds") == "2099-11-05T00:00:00-03:00"
    assert end.isoformat(timespec="seconds") == "2099-11-06T23:59:59-03:00"
    # A validade cheia não abre as catracas: a agenda 09–17 continua
    # obrigatória e atrelada ao nível Visitor aprovado.
    require_confirmed_daily_schedule(["DAY_USE"], ticket, settings["schedule_windows"])
    payload = registration_payload(
        Visitor("Teste", "Hospede", "Day use", b"", "1234", start, end))
    assert payload["visitStartTime"] == "2099-11-05T00:00:00-03:00"
    assert payload["visitEndTime"] == "2099-11-06T23:59:59-03:00"


@pytest.mark.parametrize("entry,exit_", [
    ("05/11/2099 16:44", "05/11/2099"),
    ("05/11/2099", "05/11/2099 17:00"),
    ("2026-11-05", "05/11/2099"),
    ("32/11/2099", "05/11/2099"),
    ("05/11/2099", "04/11/2099"),
    ("05/11/2099", "07/11/2099"),
])
def test_date_only_rejects_hours_bad_dates_and_wrong_duration(entry, exit_):
    from core import visitor_period_from_dates
    with pytest.raises(ValueError):
        visitor_period_from_dates(
            entry, exit_, default_settings(), default_settings()["tickets"]["1 dia"])


def test_technical_validity_hours_only_from_admin_settings(tmp_path):
    from core import visitor_period_from_dates, visitor_validity_hours
    settings = default_settings()
    assert tuple(str(x) for x in visitor_validity_hours(settings)) == (
        "00:00:00", "23:59:59")
    settings["validity_start_time"] = "00:15:00"
    settings["validity_end_time"] = "23:00:59"
    save_settings(settings, tmp_path)
    restored = load_settings(tmp_path)
    start, end = visitor_period_from_dates("05/11/2099", "05/11/2099", restored)
    assert start.strftime("%H:%M:%S") == "00:15:00"
    assert end.strftime("%H:%M:%S") == "23:00:59"
    assert restored["schedule_windows"]["DAY_USE"] == "09:00-17:00"
    settings["validity_start_time"] = "12:00:00"
    settings["validity_end_time"] = "11:59:59"
    with pytest.raises(ValueError, match="anterior"):
        save_settings(settings, tmp_path)
    assert load_settings(tmp_path) == restored


def test_date_only_editor_period_rejects_expired_during_payload_validation():
    from core import visitor_period_from_dates
    start, end = visitor_period_from_dates("01/01/2020", "01/01/2020", default_settings())
    with pytest.raises(ValueError, match="expirou"):
        registration_payload(
            Visitor("Teste", "Local", "Hóspedes Vale", b"", "1234", start, end))
