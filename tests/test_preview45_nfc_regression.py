"""Regression for field-proven NFC registration order (preview.45 -> preview.80).

This intentionally checks both code ordering and the real HikCentral wrapper
payload. No HikCentral network calls are made by these tests.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta
from io import BytesIO
import inspect
import textwrap

from PIL import Image

from core import (TZ, Visitor, registration_payload, mark_appointment_inflight,
                  save_pending, load_pending)
from hikcentral import HikCentral


def _ast_calls(node):
    # ast.walk is breadth-first, not the execution order. Use source
    # positions to guard the actual network submission sequence.
    calls = [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, (ast.Attribute, ast.Name))
    ]
    return [
        call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
        for call in sorted(calls, key=lambda c: (c.lineno, c.col_offset))
    ]


def _work_ast():
    import app
    tree = ast.parse(textwrap.dedent(inspect.getsource(app.ValeApp.register)))
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
             and n.name == "work"]
    assert len(found) == 1
    return found[0]


def test_registerment_is_sent_directly_after_appointment_without_person_writes():
    work = _work_ast()
    calls = _ast_calls(work)
    # This is the same effective order as the preview.45, which produced a
    # physical card in the user's HikCentral: book -> check-in -> permissions.
    assert calls.index("create_appointment") < calls.index("register_visitor")
    assert calls.index("register_visitor") < calls.index("assign_access")
    assert calls.index("assign_access") < calls.index("apply")
    assert "ensure_issued_card" not in calls
    assert "ensure_checkin_card" not in calls

    checkin_paths = [
        n for n in ast.walk(work)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
        and n.test.id == "checkin_requested"
    ]
    assert len(checkin_paths) == 1
    checkin = checkin_paths[0]
    checkin_calls = [x for stmt in checkin.body for x in _ast_calls(stmt)]
    reservation_only_calls = [x for stmt in checkin.orelse for x in _ast_calls(stmt)]
    assert "register_visitor" in checkin_calls
    assert "update_profile" not in checkin_calls
    assert "update_profile" in reservation_only_calls
    assert "register_visitor" not in reservation_only_calls
    assert "mark_card_confirmed" not in reservation_only_calls

    # NFC confirmation must be a READ of the already created check-in,
    # after all remote writes, never an update of Person.cards.
    card_reads = [n for n in ast.walk(work) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name)
                  and n.func.id == "confirm_checkin_card"]
    assert len(card_reads) == 1


class Recorder:
    def __init__(self):
        self.calls = []

    @staticmethod
    def access_info(levels):
        return HikCentral.access_info(levels)

    def post(self, path, body):
        self.calls.append((path, body))
        if path == "/artemis/api/visitor/v2/appointment":
            return {"data": {"appointRecordId": "775003393910374400",
                             "visitorId": "2243"}}
        if path == "/artemis/api/visitor/v1/registerment":
            return {"data": {"appointRecordId": "513"}}
        raise AssertionError("Unexpected remote call: " + path)


def test_legacy_checkin_request_includes_photo_nfc_group_and_original_ids():
    img = Image.new("RGB", (64, 64))
    out = BytesIO()
    img.save(out, format="JPEG")
    start = datetime.now(TZ) + timedelta(days=1)
    end = start + timedelta(hours=12)
    visitor = Visitor("Wesley", "Nascimento", "Hóspedes Vale",
                      out.getvalue(), "321", start, end, "123")
    info = registration_payload(visitor)
    levels = [{"id": "10", "name": "VISITANTE_VALE"}]
    api = Recorder()
    appointment, person_id = HikCentral.create_appointment(api, info, levels)
    record_id = HikCentral.register_visitor(api, info, levels, appointment, person_id)
    assert record_id == "513"
    assert len(api.calls) == 2
    booking, register = (args for _, args in api.calls)
    assert register["appointId"] == appointment
    assert register["visitorId"] == person_id
    assert booking["visitorInfoList"][0]["VisitorInfo"]["cards"] == [{"cardNo": "321"}]
    sent = register["visitorInfoList"][0]["VisitorInfo"]
    assert sent["visitorId"] == person_id
    assert sent["cards"] == [{"cardNo": "321"}]
    assert sent["faces"] == info["visitorInfoList"][0]["VisitorInfo"]["faces"]
    assert sent["visitorGroupName"] == "Hóspedes Vale"
    assert sent["watchListInfo"] == [{"enableRegister": 1, "ID": "0", "Type": "1"}]
    assert register["accessInfo"] == booking["accessInfo"]


def test_new_journal_remembers_direct_nfc_and_does_not_falsely_mark_issued(tmp_path):
    mark_appointment_inflight(tmp_path)
    save_pending(
        "2243", ["10"], tmp_path,
        appointment_id="775003393910374400",
        door_ids=["catraca1"],
        access_names=["VISITANTE_VALE"],
        entry_at="2099-11-09T00:00:00-03:00",
        exit_at="2099-11-09T23:59:59-03:00",
        expect_face=True, expect_card=True,
        checkin_requested=True, card_confirmed=False,
        direct_checkin_nfc=True, registered=False)
    pending = load_pending(tmp_path)
    assert pending["direct_checkin_nfc"] is True
    assert pending["card_confirmed"] is False
    assert pending["checkin_inflight"] is False
