"""Pre-release startup regression; no network, visitor creation or local credentials."""
import json
import os

import app
import core
import pytest


def test_all_symbols_imported_by_startup_app_exist():
    # Import at test collection already reproduces a missing from-core symbol.
    assert app.APP_VERSION == core.APP_VERSION
    assert callable(app.ValeApp)
    assert core.default_settings()["group_access"] == {
        "Day use": ["DAY_USE"],
        "Hóspedes Serra": ["VISITANTE_SERRA"],
        "Hóspedes Vale": ["VISITANTE_VALE"],
        "Visitors": [],
    }


@pytest.mark.skipif(os.name != "nt", reason="Desktop smoke uses Windows Tk")
def test_real_gui_offline_smoke_does_not_connect_to_hikcentral(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("A verificação offline não pode consultar o servidor")
    monkeypatch.setattr(app, "HikCentral", forbidden)
    monkeypatch.setattr(app, "load_pending", forbidden)
    monkeypatch.setattr(app, "save_api_keys", forbidden)
    report_path = tmp_path / "offline-smoke.json"
    assert app.offline_startup_smoke_check(report_path) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report == {
        "kind": "offline_startup_smoke",
        "version": core.APP_VERSION,
        "network_used": False,
        "visitor_created": False,
        "ok": True,
        "gui_created": True,
        "face_detector_ready": True,
    }
