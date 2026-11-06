"""Testes da transação de edição, sem contato com o HikCentral real."""
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
        "_original_appointment_id": "appt-old",
        "entry_at": OLD_START, "exit_at": OLD_END,
        "group": "Hóspedes Vale", "access_names": ["VISITANTE_VALE"],
    }


def payload():
    return {"visitStartTime": NEW_START, "visitEndTime": NEW_END}


class API:
    def __init__(self, *, new_record="check-new", returned_appt="appt-new"):
        self.new_record = new_record
        self.returned_appt = returned_appt
        self.updates = []
        self.applies = []

    def find_existing_registration(self, visitor, start, end):
        assert visitor == "1654"
        if (start, end) == (OLD_START, OLD_END):
            return "check-old"
        if (start, end) == (NEW_START, NEW_END):
            return self.new_record
        return None

    def update_registered_visitor(self, appt_id, vid, info, levels):
        self.updates.append((appt_id, vid, info, levels))
        return self.returned_appt

    def apply(self, visitor, doors):
        self.applies.append((visitor, doors))

    def delivery_report(self, visitor, doors, *, expect_face, expect_card):
        return {"state": "confirmed", "message": "simulado", "doors": []}


def use_mocks(monkeypatch, *, after_id="appt-new"):
    written, status = [], []

    def lookup(api, item):
        if item["entry_at"] == OLD_START:
            return {"appointRecordId": "appt-old"}
        return {"appointRecordId": after_id}

    monkeypatch.setattr(tx, "find_original_appointment", lookup)
    monkeypatch.setattr(tx, "save_registration", lambda row: written.append(row))
    monkeypatch.setattr(tx, "set_history_status", lambda *a, **kw: status.append((a, kw)))
    monkeypatch.setattr(tx, "make_access_receipt",
                        lambda row, *, level_id, door_ids: {
                            "confirmed_at": "2099-11-07T09:00:00-03:00",
                            "level_id": level_id, "doors": door_ids})
    return written, status


def call(api, path):
    return tx.update_existing_visit(
        api, record(), payload(),
        [{"id": "10", "name": "VISITANTE_VALE"}], ["door-a", "door-b"],
        SimpleNamespace(first_name="TESTE", last_name="TESTE3",
                        reservation="123", photo_jpeg=b"jpeg",
                        card_number="3631506494"),
        "Hóspedes Vale", ["VISITANTE_VALE"], journal_path=path)


def test_updated_dates_preserve_visitor_reservation_and_checkin_links(monkeypatch, tmp_path):
    written, status = use_mocks(monkeypatch)
    api = API()
    journal = tmp_path / "edit.json"
    response = call(api, journal)
    assert response["confirmed_appointment_id"] == "appt-new"
    assert response["confirmed_checkin_id"] == "check-new"
    assert len(api.updates) == 1
    assert api.updates[0][0] == "appt-old"  # nunca check-old
    assert api.updates[0][1] == "1654"
    assert api.applies == [("1654", ["door-a", "door-b"])]
    assert not journal.exists()
    assert written[-1]["visitor_id"] == "1654"
    assert written[-1]["appointment_id"] == "appt-new"
    assert written[-1]["registration_record_id"] == "check-new"
    assert written[-1]["entry_at"] == NEW_START
    assert written[-1]["exit_at"] == NEW_END
    assert status[-1][1]["checkin_state"] == "confirmado"


def test_update_readback_missing_does_not_change_local_or_reapply(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API(new_record=None)
    journal = tmp_path / "edit.json"
    with pytest.raises(ValueError, match="NÃO confirmou"):
        call(api, journal)
    assert len(api.updates) == 1
    assert not written and not api.applies
    assert journal.exists()
    with pytest.raises(ValueError, match="resultado incerto"):
        call(api, journal)
    assert len(api.updates) == 1


def test_reservation_does_not_match_response_blocks_without_local_change(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch, after_id="another-appointment")
    api = API()
    with pytest.raises(ValueError, match="difere"):
        call(api, tmp_path / "edit.json")
    assert len(api.updates) == 1
    assert not written and not api.applies


def test_missing_original_reservation_id_blocks_before_update(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    monkeypatch.setattr(tx, "find_original_appointment",
                        lambda *a, **kw: {"reservationCode": "7155"})
    api = API()
    with pytest.raises(ValueError, match="appointRecordId"):
        call(api, tmp_path / "edit.json")
    assert not api.updates and not written
    assert not (tmp_path / "edit.json").exists()


def test_edited_visitor_identity_change_blocks_before_write(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    wrong = record()
    wrong["_original_appointment_id"] = "old-unrelated"
    api = API()
    with pytest.raises(ValueError, match="ID da reserva mudou"):
        tx.update_existing_visit(
            api, wrong, payload(),
            [{"id": "10", "name": "VISITANTE_VALE"}], ["door-a"],
            SimpleNamespace(first_name="Teste", last_name="Teste3",
                            reservation="", photo_jpeg=b"", card_number="12"),
            "Hóspedes Vale", ["VISITANTE_VALE"],
            journal_path=tmp_path / "edit.json")
    assert not api.updates and not written


def test_remote_post_exception_preserves_journal_for_manual_reconciliation(monkeypatch, tmp_path):
    written, _ = use_mocks(monkeypatch)
    api = API()
    def unknown(*args):
        raise HikError("timeout após envio")
    api.update_registered_visitor = unknown
    journal = tmp_path / "edit.json"
    with pytest.raises(HikError, match="timeout"):
        call(api, journal)
    assert journal.exists() and not written and not api.applies
