"""Payload requirements and optional field preservation; no network calls."""
from copy import deepcopy

import pytest

from edit_errors import EditorFieldError
from edit_request import preserve_visitor_metadata, validate_edit_request


def valid_request():
    return {
        "visitStartTime": "2099-11-09T00:00:00-03:00",
        "visitEndTime": "2099-11-10T23:59:59-03:00",
        "visitorInfoList": [{"VisitorInfo": {
            "visitorGivenName": "Nome novo", "visitorFamilyName": "Sobrenome novo",
            "visitorGroupName": "Hóspedes Serra", "remark": "903",
            "gender": 0, "companyName": "Vale", "certificateType": 111,
            "certificateNo": "", "cards": [{"cardNo": "12345678"}],
        }}],
    }


def test_optional_metadata_is_preserved_without_replacing_edited_fields_or_credentials():
    payload = valid_request()
    details = {"VisitorInfo": {
        "visitorId": "1426", "visitorGivenName": "Nome antigo",
        "visitorFamilyName": "Sobrenome antigo", "visitorGroupName": "Hóspedes Vale",
        "remark": "RESERVA ANTIGA", "cards": [{"cardNo": "OLD-CARD"}],
        "faces": [{"faceData": "OLD-FACE"}], "gender": 2, "companyName": "Empresa original",
        "certificateType": 100, "certificateNo": "DOCUMENTO-ORIGINAL",
        "phoneNo": "001234", "email": "original@example.invalid", "plateNo": "TEST1234",
        "customFieldList": [{"id": "original", "value": "preservar"}],
        "watchListInfo": [{"enableRegister": 1, "ID": "7", "Type": "1"}],
    }}
    before = deepcopy((payload, details))
    complete = preserve_visitor_metadata(payload, details)
    info = complete["visitorInfoList"][0]["VisitorInfo"]
    for field in ("gender", "companyName", "certificateType", "certificateNo", "phoneNo",
                  "email", "plateNo", "customFieldList", "watchListInfo"):
        assert info[field] == details["VisitorInfo"][field]
    for field in ("visitorGivenName", "visitorFamilyName", "visitorGroupName", "remark", "cards"):
        assert info[field] == payload["visitorInfoList"][0]["VisitorInfo"][field]
    assert "faces" not in info
    assert "visitorId" not in info
    assert complete["visitStartTime"] == payload["visitStartTime"]
    info["customFieldList"][0]["value"] = "mutado apenas na cópia"
    assert (payload, details) == before


def test_required_protocol_defaults_are_supplied_when_remote_optional_fields_are_missing():
    payload = valid_request()
    payload["visitorInfoList"][0]["VisitorInfo"].pop("gender")
    complete = preserve_visitor_metadata(payload, {"VisitorInfo": {"visitorId": "1426"}})
    info = complete["visitorInfoList"][0]["VisitorInfo"]
    assert info["gender"] == 0
    assert info["watchListInfo"] == [{"enableRegister": 1, "ID": "0", "Type": "1"}]
    validate_edit_request(complete)


def test_missing_remote_optional_value_does_not_overwrite_payload_default():
    payload = valid_request()
    complete = preserve_visitor_metadata(payload, {"VisitorInfo": {
        "gender": None, "companyName": None, "certificateNo": None}})
    assert complete["visitorInfoList"][0]["VisitorInfo"]["gender"] == 0
    assert complete["visitorInfoList"][0]["VisitorInfo"]["companyName"] == "Vale"


@pytest.mark.parametrize("field,value,error_field", [
    ("visitStartTime", "2099-11-09T00:00:00", "entry"),
    ("visitEndTime", "2099-11-08T00:00:00-03:00", "exit"),
])
def test_request_validation_identifies_the_date_field_to_correct(field, value, error_field):
    payload = valid_request()
    payload[field] = value
    with pytest.raises(EditorFieldError) as error:
        validate_edit_request(payload)
    assert error_field in error.value.fields


@pytest.mark.parametrize("key,value,error_field", [
    ("visitorGivenName", "", "first"),
    ("visitorFamilyName", "", "last"),
    ("visitorGroupName", "", "group"),
    ("cards", [{"cardNo": "!INVALID!"}], "card"),
    ("faces", [{"faceData": ""}], "photo"),
])
def test_request_validation_identifies_the_missing_person_field(key, value, error_field):
    payload = valid_request()
    payload["visitorInfoList"][0]["VisitorInfo"][key] = value
    with pytest.raises(EditorFieldError) as error:
        validate_edit_request(payload)
    assert error_field in error.value.fields
