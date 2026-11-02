import json

import app


def test_readonly_exe_check_writes_only_sanitized_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "load_settings", lambda: {
        "host": "127.0.0.1", "port": 443, "ca_file": "",
        "cert_pin_sha256": "A" * 64,
        "group_access": {"Day use": ["VISITANTE"]}
    })
    monkeypatch.setattr(app, "load_api_keys", lambda: ("private-key", "private-secret"))

    class FakeClient:
        def __init__(self, *args, **kwargs):
            assert "private-key" in args and "private-secret" in args

        def access_levels(self):
            return [{"id": "1"}]

        def visitor_groups(self):
            return ["Day use"]

        def resolve_levels(self, requested):
            assert requested == ["VISITANTE"]
            return ([{"id": "VISITOR-01", "name": "VISITANTE"}],
                    [f"access-{i}" for i in range(10)])

    monkeypatch.setattr(app, "HikCentral", FakeClient)
    report_path = tmp_path / "check.json"
    assert app.readonly_api_check(report_path) == 0
    result = json.loads(report_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["visitor_group_count"] == 1
    assert result["visitor_level_count"] == 1
    assert result["visitor_level_type"] == 2
    assert result["configured_group_mappings"]["Day use"] == {
        "mapping_valid": True, "level_name": "VISITANTE", "point_count": 10
    }
    assert result["mapped_group_count"] == 1
    assert result["openhikcentral_writes_performed"] is False
    assert result["effective_schedule_readback"] == "not_verified_by_this_API_check"
    assert result["opencv_imported"] is True
    assert result["pcsc_library_imported"] is True
    assert "private-key" not in report_path.read_text(encoding="utf-8")
    assert "private-secret" not in report_path.read_text(encoding="utf-8")


def test_readonly_exe_check_records_failure_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "load_settings", lambda: {
        "host": "127.0.0.1", "port": 443, "ca_file": "", "cert_pin_sha256": ""
    })
    monkeypatch.setattr(app, "load_api_keys", lambda: ("key", "secret"))

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def access_levels(self):
            raise RuntimeError("Partner permission denied")

    monkeypatch.setattr(app, "HikCentral", FakeClient)
    report_path = tmp_path / "failed.json"
    assert app.readonly_api_check(report_path) == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert "permission denied" in report["error"]
    assert "secret" not in report_path.read_text(encoding="utf-8")
