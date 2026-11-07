from datetime import datetime
from io import BytesIO

from PIL import Image
import pytest

from core import TZ, default_settings
from editor_validation import (editor_requirements, infer_edit_operation, reservation_edit_dates,
                               validate_editor_fields)


def values():
    return {"first": "Wesley", "last": "123 Nascimento", "reservation": "903",
            "entry": "09/11/2099", "exit": "10/11/2099", "card": "12345678",
            "group": "Hóspedes Serra", "credential_mode": "Somente pulseira",
            "appointment": "booking-original", "operation": "Atualizar visita existente"}


def jpeg():
    stream = BytesIO()
    Image.new("RGB", (20, 20), "white").save(stream, "JPEG")
    return stream.getvalue()


def validate(fields=None, **kwargs):
    return validate_editor_fields(values() if fields is None else fields,
                                  default_settings(), ["Hóspedes Serra"], **kwargs)


def test_closed_visit_with_photo_download_failure_requires_replacement_not_readonly():
    requirements = editor_requirements({"checkin_state": "check-out"}, photo_error="código128")
    assert requirements["require_photo"] is True
    fields = values()
    fields["credential_mode"] = "Foto e pulseira"
    assert set(validate(fields, **requirements)) == {"photo"}
    assert validate(fields, photo_bytes=jpeg(), **requirements) == {}


def test_each_missing_required_value_has_its_own_actionable_field_error():
    fields = {key: "" for key in values()}
    errors = validate(fields)
    assert set(errors) == {"first", "last", "entry", "exit", "group", "photo", "card", "credential_mode"}
    assert "reservation" not in errors  # HikCentral remark is optional, still sent as an empty field.


def test_known_nfc_cannot_be_silently_omitted_when_a_photo_is_provided():
    fields = values()
    fields["credential_mode"] = "Foto e pulseira"
    fields["card"] = ""
    required = editor_requirements({"expect_card": True})
    assert "card" in validate(fields, photo_bytes=jpeg(), **required)
    fields["card"] = "12345678"
    assert validate(fields, photo_bytes=jpeg(), **required) == {}


@pytest.mark.parametrize("field,value", [("first", ""), ("last", "X"),
                                        ("entry", "31/02/2099"),
                                        ("exit", "01/11/2099"),
                                        ("card", "12 34"),
                                        ("group", "Funcionários")])
def test_invalid_value_marks_the_corresponding_field(field, value):
    fields = values()
    fields[field] = value
    assert field in validate(fields)


def test_expired_visit_dates_can_be_edited_as_history():
    fields = values()
    fields.update(entry="06/11/2026", exit="06/11/2026")
    assert validate(fields, now=datetime(2026, 11, 7, tzinfo=TZ)) == {}


def test_valid_card_only_payload_needs_no_invented_photo():
    assert validate() == {}


def test_no_credential_metadata_requires_explicit_operator_mode_selection():
    requirements = editor_requirements({})
    assert requirements["initial_credential_mode"] == ""
    fields = values()
    fields["credential_mode"] = ""
    assert "credential_mode" in validate(fields, **requirements)
    fields["credential_mode"] = "Somente pulseira"
    assert validate(fields, **requirements) == {}


def test_mode_must_include_every_credential_to_be_sent():
    assert "credential_mode" in validate(photo_bytes=jpeg())


def test_renewal_requires_the_original_booking_id_before_send():
    fields = values()
    fields.update(operation="Renovar visita", appointment="")
    assert "appointment" in validate(fields)
    fields["appointment"] = "773498088366014464"
    assert validate(fields) == {}


def test_unknown_remote_state_requires_an_explicit_operation():
    fields = values()
    fields["operation"] = ""
    required = editor_requirements({})
    assert "operation" in validate(fields, **required)
    fields["operation"] = "Atualizar visita existente"
    assert validate(fields, **required) == {}


@pytest.mark.parametrize("state", ["confirmado", "check-in efetuado"])
def test_successfully_saved_active_visit_does_not_require_hidden_operation(state):
    record = {"checkin_state": state}
    assert infer_edit_operation(record) == "update"
    requirements = editor_requirements(record)
    assert requirements["require_operation"] is False
    fields = values()
    fields["operation"] = ""
    assert "operation" not in validate(fields, **requirements)


def test_active_visit_mismatch_shows_visit_dates_and_keeps_old_reservation_as_baseline():
    displayed, baseline = reservation_edit_dates({
        "checkin_state": "check-in efetuado",
        "entry_at": "2026-11-07T00:00:00-03:00",
        "exit_at": "2026-11-08T23:59:59-03:00",
        "_reservation_period": {"state": "different",
            "entry_at": "2026-11-07T00:00:00-03:00",
            "exit_at": "2026-11-07T23:59:59-03:00"},
    })
    assert displayed == {"entry": "07/11/2026", "exit": "08/11/2026"}
    assert baseline == {"entry": "07/11/2026", "exit": "07/11/2026"}


def test_closed_historical_mismatch_keeps_reservation_dates_as_safe_defaults():
    displayed, baseline = reservation_edit_dates({
        "checkin_state": "check-out",
        "entry_at": "2026-11-07T00:00:00-03:00",
        "exit_at": "2026-11-08T23:59:59-03:00",
        "_reservation_period": {"state": "different",
            "entry_at": "2026-11-07T00:00:00-03:00",
            "exit_at": "2026-11-07T23:59:59-03:00"},
    })
    assert displayed == baseline == {"entry": "07/11/2026", "exit": "07/11/2026"}
