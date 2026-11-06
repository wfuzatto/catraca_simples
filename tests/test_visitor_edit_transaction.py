"""Edição de check-in ativo: dados/datas confirmados, sem exigir reserva auxiliar."""
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
    def __init__(self, *, new_record="check-new", returned_id="check-new"):
        self.new_record = new_record
        self.returned_id = returned_id
        self.old_record = "check-old"
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
        self.applies = []
        self.assignments = []

    def find_registration_record(self, visitor, start, end, *, active_only=True):
        assert visitor == "1654"
        assert active_only is True
        self.reads.append((visitor, start, end, active_only))
        if (start, end) == (OLD_START, OLD_END):
            record_id, group = self.old_record, self.original_group
        elif (start, end) == (NEW_START, NEW_END):
            record_id = self.new_reads.pop(0) if self.new_reads else self.new_record
            group = self.remote_group
        else:
            return None
        if not record_id:
            return None
        return {
            "recordId": record_id, "visitorStatus": "0",
            "visitorCardInfo": {"cardNumber": self.remote_card},
            "visitorBaseInfo": {
                "visitorId": visitor, "visitStartTime": start, "visitEndTime": end,
                "visitorGroup": group, "fullName": "TESTE TESTE3", "remark": "123",
            },
        }

    def find_existing_registration(self, visitor, start, end):
        row = self.find_registration_record(visitor, start, end, active_only=True)
        return row["recordId"] if row else None

    def update_registered_visitor(self, record_id, vid, info, levels):
        self.updates.append((record_id, vid, info, levels))
        return self.returned_id

    def post(self, *args, **kwargs):
        pytest.fail("A transação não deve exigir appointmentlist nem criar uma reserva.")

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
    response = call(api, journal)
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
    assert status[-1][1]["checkin_state"] == "confirmado"


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
    with pytest.raises(ValueError, match="Check-in original"):
        call(api, journal)
    assert not api.updates and not written
    assert not journal.exists()


@pytest.mark.parametrize("field,value", [
    ("detail_first", "NOME ANTIGO"),
    ("detail_last", "SOBRENOME ANTIGO"),
    ("detail_reservation", "RESERVA ANTIGA"),
    ("detail_visitor", "outra-pessoa"),
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
