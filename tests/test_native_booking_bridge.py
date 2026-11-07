from copy import deepcopy

import pytest

from edit_errors import EditorFieldError
from native_booking_bridge import build_appointment_request


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


def test_builds_same_visitor_v3_request_with_new_dates_and_full_access(booking_metadata):
    profile, original = booking_metadata
    request = _build(profile, original)
    appointment = request["AppointmentInfo"]
    visitor = appointment["VisitorInfo"]
    visit = appointment["AccessInfo"]["VisitInfoList"][0]

    assert visitor["ID"] == 1865
    assert visitor["BaseInfo"]["VisitorGroupID"] == 3
    assert visitor["BaseInfo"]["GivenName"] == "enc:Teste"
    assert visitor["BaseInfo"]["FamilyName"] == "enc:Sobrenome"
    assert visitor["BaseInfo"]["FullName"] == "enc:Teste Sobrenome"
    assert visitor["BaseInfo"]["Description"] == "enc:321"
    assert visitor["AdditionalInfo"] == profile["AdditionalInfo"]
    assert appointment["AccessInfo"]["VisitorReason"] == 4
    assert appointment["AccessInfo"]["VisitorReasonDetail"] == "Hospedagem / Day use"
    assert visit["BeginDate"] == "2026-11-16T00:00:00-03:00"
    assert visit["EndDate"] == "2026-11-17T23:59:59-03:00"
    assert visit["AccessLevelList"] == [{"ID": "7", "Name": "VISITANTE_VALE"}]
    assert appointment["WatchListInfo"] == {}
    assert "cards" not in visitor and "faces" not in visitor


def test_create_recovery_uses_current_profile_without_deleted_booking(booking_metadata):
    profile, _original = booking_metadata
    request = _build(profile, None)
    appointment = request["AppointmentInfo"]
    assert appointment["VisitorInfo"]["ID"] == 1865
    assert appointment["AccessInfo"]["VisitorReason"] == 4
    assert appointment["AccessInfo"]["VisitInfoList"][0]["AccessLevelList"] == [
        {"ID": "7", "Name": "VISITANTE_VALE"}
    ]


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
