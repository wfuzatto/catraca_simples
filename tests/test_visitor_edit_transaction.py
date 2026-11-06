"""Edição de registros de qualquer estado: campos completos e confirmação remota."""
from types import SimpleNamespace
import pytest

import visitor_edit_transaction as tx
from hikcentral import HikError


OLD_START = "2099-11-05T00:00:00-03:00"
OLD_END = "2099-11-05T23:59:59-03:00"
NEW_START = "2099-11-07T00:00:00-03:00"
NEW_END = "2099-11-08T23:59:59-03:00"


def record():
    return {
        "visitor_id": "1654", "registration_record_id": "check-old",
        "appointment_id": "booking-old",
        "entry_at": OLD_START, "exit_at": OLD_END,
        "group": "Hóspedes Vale", "access_names": ["VISITANTE_VALE"],
    }


def payload():
    return {"visitStartTime": NEW_START, "visitEndTime": NEW_END,
            "visitorInfoList": [{"VisitorInfo": {
                "visitorGroupName": "Hóspedes Vale",
                "visitorGivenName": "TESTE", "visitorFamilyName": "TESTE3",
                "remark": "123", "cards": [{"cardNo": "3631506494"}]}}]}


class API:
    def __init__(self, *, new_record="check-new", returned_id="check-new",
                 old_status="0", new_status="0"):
        self.new_record = new_record
        self.returned_id = returned_id
        self.old_record = "check-old"
        self.old_status, self.new_status = old_status, new_status
        self.remote_group = "Hóspedes Vale"
        self.remote_card = "3631506494"
        self.original_group = "Hóspedes Vale"
        self.detail_first = "TESTE"
        self.detail_last = "TESTE3"
        self.detail_reservation = "123"
        self.detail_visitor = "1654"
        self.new_reads = []
        self.reads = []
        self.updates = []
        self.renewals = []
        self.renewal_appointments = []
        self.applies = []
        self.assignments = []
        self.reservation_rows = None
        self.reservation_reads = []

    def find_registration_record(self, visitor, start, end, *, active_only=False, record_id=""):
        assert visitor == "1654"
        self.reads.append((visitor, start, end, active_only, record_id))
        if (start, end) == (OLD_START, OLD_END):
            assert active_only is False
            found_id, group, status = self.old_record, self.original_group, self.old_status
        elif (start, end) == (NEW_START, NEW_END):
            found_id = self.new_reads.pop(0) if self.new_reads else self.new_record
            group, status = self.remote_group, self.new_status
        else:
            return None
        if not found_id or (record_id and record_id != found_id) or (active_only and status != "0"):
            return None
        return {
            "recordId": found_id, "visitorStatus": status,
            "visitorCardInfo": {"cardNumber": self.remote_card},
            "visitorBaseInfo": {
                "visitorId": visitor, "visitStartTime": start, "visitEndTime": end,
                "visitorGroup": group, "fullName": "TESTE TESTE3", "remark": "123",
            },
        }

    def find_existing_registration(self, visitor, start, end):
        row = self.find_registration_record(visitor, start, end, active_only=False)
        return row["recordId"] if row else None

    def update_registered_visitor(self, record_id, vid, info, levels):
        self.updates.append((record_id, vid, info, levels))
        return self.returned_id

    def register_existing_visitor(self, visitor_id, info, levels, *, appointment_id=""):
        assert appointment_id
        self.renewals.append((visitor_id, info, levels))
        self.renewal_appointments.append(appointment_id)
        return self.returned_id

    def post(self, path, payload, **kwargs):
        from remote_history import APPOINTMENTS_PATH
        assert path == APPOINTMENTS_PATH, "A conferência nunca deve criar ou editar uma reserva."
        self.reservation_reads.append(payload)
        if self.reservation_rows is None:
            raise HikError("Consulta de reservas indisponível neste cenário.")
        return {"data": {"list": self.reservation_rows, "total": len(self.reservation_rows)}}

    def assign_access(self, visitor, level_id):
        self.assignments.append((visitor, str(level_id)))

    def apply(self, visitor, doors):
        self.applies.append((visitor, doors))

    def delivery_report(self, visitor, doors, *, expect_face, expect_card):
        return {"state": "confirmed", "message": "simulado", "doors": []}


def use_mocks(monkeypatch):
    written, status = [], []
    monkeypatch.setattr(tx, "save_registration", lambda row: written.append(row))
    monkeypatch.setattr(tx, "set_history_status", lambda *a, **kw: status.append((a, kw)))
    import remote_history

    def details(api, visitor_id):
        # O servidor real pode omitir grupo neste endpoint; o check-in o informa.
        return {"VisitorInfo": {
            "visitorId": api.detail_visitor,
            "visitorGivenName": api.detail_first,
            "visitorFamilyName": api.detail_last,
            "remark": api.detail_reservation,
        }}

    monkeypatch.setattr(remote_history, "visitor_details", details)
    monkeypatch.setattr(tx, "visitor_details", details, raising=False)
    monkeypatch.setattr(tx, "find_original_appointment", lambda *a, **kw: pytest.fail(
        "Reserva auxiliar ausente não pode impedir edição de check-in ativo."), raising=False)
    if hasattr(tx, "time"):
        monkeypatch.setattr(tx.time, "sleep", lambda _delay: None)
    monkeypatch.setattr(tx, "make_access_receipt",
                        lambda row, *, level_id, door_ids: {
                            "confirmed_at": "2099-11-07T09:00:00-03:00",
                            "level_id": level_id, "doors": door_ids})
    return written, status


def call(api, path, *, item=None):
    return tx.update_existing_visit(
        api, record() if item is None else item, payload(),
        [{"id": "10", "name": "VISITANTE_VALE"}], ["door-a", "door-b"],
        SimpleNamespace(first_name="TESTE", last_name="TESTE3",
                        reservation="123", photo_jpeg=b"jpeg",
                        card_number="3631506494"),
        "Hóspedes Vale", ["VISITANTE_VALE"], journal_path=path)


def test_edit_active_checkin_without_reservation_updates_data_and_dates(monkeypatch, tmp_path):
    written, status = use_mocks(monkeypatch)
    api = API()
    journal = tmp_path / "edit.json"
    item = record()
    item.pop("appointment_id")
    response = call(api, journal, item=item)
    assert response["confirmed_checkin_id"] == "check-new"
    assert not response.get("confirmed_appointment_id")
    assert len(api.updates) == 1
    # registerment/update edita o registro ativo verificado, não uma reserva auxiliar.
    assert api.updates[0][0] == "check-old"
    assert api.updates[0][1] == "1654"
    assert api.applies == [("1654", ["door-a", "door-b"])]
    assert api.assignments == [("1654", "10")]
    assert response["confirmed_group"] == "Hóspedes Vale"
    assert not journal.exists()
    saved = written[-1]
    assert saved["visitor_id"] == "1654"
    assert saved["registration_record_id"] == "check-new"
    assert saved["first"] == "TESTE" and saved["last"] == "TESTE3"
    assert saved["reservation"] == "123"
    assert saved["entry_at"] == NEW_START and saved["exit_at"] == NEW_END
    assert saved["checkin_state"] == "check-in efetuado"
    assert status[-1][1]["checkin_state"] == "check-in efetuado"


@pytest.mark.parametrize("returned_id", ["", "check-new"])
def test_update_response_id_optional_and_reconciled_with_checkin(monkeypatch, tmp_path, returned_id):
    written, _ = use_mocks(monkeypatch)
    api = API(returned_id=returned_id)
    response = call(api, tmp_path / "edit.json")
    assert response["confirmed_checkin_id"] == "check-new"
    assert written[-1]["registration_record_id"] == "check-new"
    assert len(api.updates) == 1


def test_known_booking_id_is_preserved_and_not_replaced_by_checkin_id(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    item = {**record(), "appointment_id": "booking-original"}
    result = call(API(), tmp_path / "edit.json", item=item)
    assert result.get("confirmed_appointment_id") == "booking-original"
    assert written[-1]["appointment_id"] == "booking-original"
    assert written[-1]["registration_record_id"] == "check-new"


def test_edited_visit_reports_original_booking_dates_without_claiming_sync(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    api.reservation_rows = [{"appointID": "booking-old", "appointCode": "3911",
        "appointStartTime": OLD_START, "appointEndTime": OLD_END,
        "visitorInfo": {"visitorId": "1654"}}]
    result = call(api, tmp_path / "edit.json")
    assert result["reservation_period"]["state"] == "different"
    assert result["reservation_period"]["entry_at"] == OLD_START
    assert written[-1]["entry_at"] == NEW_START
    assert written[-1]["reservation_entry_at"] == OLD_START
    assert len(api.updates) == 1 and not api.renewals
    assert len(api.reservation_reads) == 1


def test_unavailable_booking_does_not_prevent_edit_or_claim_dates_match(monkeypatch, tmp_path):
    use_mocks(monkeypatch)
    api = API()
    result = call(api, tmp_path / "edit.json")
    assert result["reservation_period"]["state"] == "unavailable"
    assert result["confirmed_checkin_id"] == "check-new"


def test_delayed_checkin_readback_retries_reads_without_another_update(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    api.new_reads = [None]
    response = call(api, tmp_path / "edit.json")
    assert response["confirmed_checkin_id"] == "check-new"
    assert len(api.updates) == 1
    assert len([read for read in api.reads if read[1] == NEW_START]) >= 2
    assert written[-1]["entry_at"] == NEW_START


def test_update_readback_missing_does_not_change_local_or_reapply(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(new_record=None)
    journal = tmp_path / "edit.json"
    with pytest.raises(ValueError, match="confirm"):
        call(api, journal)
    assert len(api.updates) == 1
    assert not written and not api.applies and not api.assignments
    assert journal.exists()
    with pytest.raises(ValueError):
        call(api, journal)
    assert len(api.updates) == 1


def test_pending_acknowledged_edit_resumes_readback_without_second_post(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(new_record=None)
    journal = tmp_path / "edit.json"
    with pytest.raises(ValueError):
        call(api, journal)
    assert len(api.updates) == 1 and not written
    api.new_record = "check-new"
    result = call(api, journal)
    assert result["confirmed_checkin_id"] == "check-new"
    assert len(api.updates) == 1
    assert written[-1]["registration_record_id"] == "check-new"
    assert not journal.exists()


def test_returned_record_id_must_match_confirmed_checkin(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(returned_id="unrelated-record")
    journal = tmp_path / "edit.json"
    with pytest.raises(ValueError):
        call(api, journal)
    assert len(api.updates) == 1
    assert not written and not api.applies and not api.assignments
    assert journal.exists()


def test_original_checkin_changed_blocks_before_write(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    api.old_record = "another-checkin"
    journal = tmp_path / "edit.json"
    with pytest.raises(ValueError, match="[Cc]heck-in original"):
        call(api, journal)
    assert not api.updates and not written
    assert not journal.exists()


@pytest.mark.parametrize("field,value", [
    ("detail_first", "NOME ANTIGO"),
    ("detail_last", "SOBRENOME ANTIGO"),
    ("detail_reservation", "RESERVA ANTIGA"),
    ("remote_card", "PULSEIRA-ANTIGA"),
])
def test_unpersisted_person_data_never_reports_success(monkeypatch, tmp_path, field, value):
    written, _ = use_mocks(monkeypatch)
    api = API()
    setattr(api, field, value)
    journal = tmp_path / "edit.json"
    with pytest.raises(ValueError):
        call(api, journal)
    assert len(api.updates) == 1
    assert not written and not api.applies and not api.assignments
    assert journal.exists()


def test_wrong_person_detail_is_rejected_before_any_write(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    from edit_errors import EditorFieldError
    api = API()
    api.detail_visitor = "outra-pessoa"
    journal = tmp_path / "edit.json"
    with pytest.raises(EditorFieldError) as error:
        call(api, journal)
    assert "visit" in error.value.fields
    assert not api.updates and not api.renewals and not written
    assert not api.applies and not api.assignments and not journal.exists()


def test_remote_post_exception_preserves_journal_and_never_sends_again(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    sent = []

    def unknown(*args):
        sent.append(args)
        raise HikError("timeout após envio")

    api.update_registered_visitor = unknown
    journal = tmp_path / "edit.json"
    with pytest.raises(HikError, match="timeout") as err:
        call(api, journal)
    assert "Consulta posterior" in str(err.value)
    import json
    state = json.loads(journal.read_text(encoding="utf-8"))
    assert state["status"] == "update_refused_or_uncertain"
    assert journal.exists() and not written and not api.applies
    with pytest.raises(ValueError):
        call(api, journal)
    assert len(sent) == 1


def test_visitor_group_change_to_serra_is_blocked_before_post(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    with pytest.raises(ValueError, match="Alteração de grupo"):
        tx.update_existing_visit(
            api, record(), payload(),
            [{"id": "20", "name": "VISITANTE_SERRA"}], ["door-other"],
            SimpleNamespace(first_name="TESTE", last_name="TESTE3",
                            reservation="123", photo_jpeg=b"foto", card_number="123"),
            "Hóspedes Serra", ["VISITANTE_SERRA"],
            journal_path=tmp_path / "edit.json")
    assert not api.updates and not api.assignments and not written
    assert not (tmp_path / "edit.json").exists()


def test_payload_cannot_change_group_even_if_ui_was_manipulated(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    altered = payload()
    altered["visitorInfoList"][0]["VisitorInfo"]["visitorGroupName"] = "Hóspedes Serra"
    with pytest.raises(ValueError, match="payload altera o grupo original"):
        tx.update_existing_visit(
            api, record(), altered,
            [{"id": "10", "name": "VISITANTE_VALE"}], ["door-a"],
            SimpleNamespace(first_name="TESTE", last_name="TESTE3",
                            reservation="123", photo_jpeg=b"foto", card_number="123"),
            "Hóspedes Vale", ["VISITANTE_VALE"],
            journal_path=tmp_path / "edit.json")
    assert not api.updates and not api.assignments and not written


@pytest.mark.parametrize("group", ["Hóspedes Serra", ""])
def test_remote_checkin_group_is_required_even_if_person_detail_omits_it(monkeypatch, tmp_path, group):
    written, _ = use_mocks(monkeypatch)
    api = API()
    api.remote_group = group
    journal = tmp_path / "edit.json"
    with pytest.raises(ValueError, match="[Gg]rupo"):
        call(api, journal)
    assert len(api.updates) == 1
    assert not api.assignments and not api.applies and not written
    assert journal.exists()
    with pytest.raises(ValueError):
        call(api, journal)
    assert len(api.updates) == 1


def test_update_refusal_128_checks_both_dates_without_second_post(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(new_record=None)
    sent = []

    def rejected(*args):
        sent.append(args)
        raise HikError("HikCentral recusou a operação (HTTP 200; código 128)")

    api.update_registered_visitor = rejected
    journal = tmp_path / "edit.json"
    with pytest.raises(HikError, match="código 128") as exc:
        call(api, journal)
    assert "datas antigas = true" in str(exc.value)
    assert "datas novas = false" in str(exc.value)
    assert len(sent) == 1 and not written and not api.assignments
    with pytest.raises(ValueError, match="resultado incerto"):
        call(api, journal)
    assert len(sent) == 1


@pytest.mark.parametrize("old_status", ["1", "2", "3", "4"],
                         ids=["checked-out", "auto-out", "terminal-out", "late-out"])
def test_closed_registration_renews_same_person_with_complete_fields(monkeypatch, tmp_path, old_status):
    written, _ = use_mocks(monkeypatch)
    api = API(old_status=old_status, new_status="0")
    item = {**record(), "checkin_state": "check-out"}
    result = call(api, tmp_path / "edit.json", item=item)
    assert result["confirmed_checkin_id"] == "check-new"
    assert result["operation"] == "renew"
    assert not api.updates and len(api.renewals) == 1
    assert api.renewal_appointments == ["booking-old"]
    visitor_id, sent, _ = api.renewals[0]
    assert visitor_id == "1654"
    info = sent["visitorInfoList"][0]["VisitorInfo"]
    assert (info["visitorGivenName"], info["visitorFamilyName"], info["remark"]) == ("TESTE", "TESTE3", "123")
    assert info["cards"][0]["cardNo"] == "3631506494"
    assert (sent["visitStartTime"], sent["visitEndTime"]) == (NEW_START, NEW_END)
    assert written[-1]["visitor_id"] == "1654"
    assert api.reads[0][3] is False and api.reads[0][4] == "check-old"


def test_renewal_must_confirm_an_active_visit_before_success(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(old_status="1", new_status="1")
    with pytest.raises(ValueError):
        call(api, tmp_path / "edit.json")
    assert not api.updates and len(api.renewals) == 1
    assert not written and not api.assignments and not api.applies


def test_renewal_refusal_never_retries_or_falls_back_to_update(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(old_status="1", new_record=None)
    sent = []

    def reject(*args, **kwargs):
        sent.append((args, kwargs))
        raise HikError("recusa de renovação: código 128")

    api.register_existing_visitor = reject
    journal = tmp_path / "edit.json"
    with pytest.raises(HikError, match="128"):
        call(api, journal)
    assert len(sent) == 1 and not api.updates and not written
    assert journal.exists()
    with pytest.raises(ValueError):
        call(api, journal)
    assert len(sent) == 1


def test_missing_registration_id_is_recovered_by_unique_remote_record_before_write(monkeypatch, tmp_path):
    use_mocks(monkeypatch)
    api = API()
    item = {**record(), "registration_record_id": ""}
    result = call(api, tmp_path / "edit.json", item=item)
    assert result["confirmed_checkin_id"] == "check-new"
    assert len(api.updates) == 1 and api.updates[0][0] == "check-old"
    assert api.reads[0][4] == ""


def test_missing_group_can_use_operator_explicit_selection(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    item = {**record(), "group": "", "_operator_selected_group": True}
    result = call(api, tmp_path / "edit.json", item=item)
    assert result["confirmed_group"] == "Hóspedes Vale"
    assert len(api.updates) == 1
    assert written[-1]["group"] == "Hóspedes Vale"


@pytest.mark.parametrize("missing", [
    "visitorInfoList", "visitorGivenName", "visitorFamilyName", "visitorGroupName",
    "visitStartTime", "visitEndTime", "credentials",
])
def test_missing_required_request_fields_never_reach_any_remote_call(monkeypatch, tmp_path, missing):
    use_mocks(monkeypatch)
    api = API()
    requested = payload()
    if missing in ("visitStartTime", "visitEndTime", "visitorInfoList"):
        requested.pop(missing)
    elif missing == "credentials":
        requested["visitorInfoList"][0]["VisitorInfo"].pop("cards")
    else:
        requested["visitorInfoList"][0]["VisitorInfo"].pop(missing)
    with pytest.raises(ValueError):
        tx.update_existing_visit(
            api, record(), requested,
            [{"id": "10", "name": "VISITANTE_VALE"}], ["door-a"],
            SimpleNamespace(first_name="TESTE", last_name="TESTE3", reservation="123",
                            photo_jpeg=b"jpeg", card_number="3631506494"),
            "Hóspedes Vale", ["VISITANTE_VALE"], journal_path=tmp_path / "edit.json")
    assert not api.reads and not api.updates and not api.renewals


def test_renewal_preserves_verified_original_booking_for_future_edits(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(old_status="1", new_status="0")
    item = {**record(), "appointment_id": "old-booking", "_original_appointment_id": "old-booking"}
    result = call(api, tmp_path / "edit.json", item=item)
    assert result["operation"] == "renew"
    assert result["confirmed_appointment_id"] == "old-booking"
    assert written[-1].get("appointment_id") == "old-booking"
    assert not api.updates and len(api.renewals) == 1


def test_closed_visit_missing_booking_requires_operator_selection_before_write(monkeypatch, tmp_path):
    use_mocks(monkeypatch)
    from edit_errors import EditorFieldError
    api = API(old_status="1")
    item = record()
    item.pop("appointment_id")
    journal = tmp_path / "edit.json"
    with pytest.raises(EditorFieldError) as error:
        call(api, journal, item=item)
    assert "appointment" in error.value.fields
    assert not api.updates and not api.renewals
    assert not journal.exists()
