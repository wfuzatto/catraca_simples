"""Testa o layout do EXE Windows sem acesso ao HikCentral."""
import os
import pytest


@pytest.mark.skipif(os.name != "nt", reason="O teste de geometria exige Windows com Tk.")
def test_register_button_visible_and_form_is_simplified(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.simpledialog, "askstring", lambda *a, **kw: "test-only-password")
    monkeypatch.setattr(app, "verify_settings_password", lambda _: True)
    root = app.ValeApp()
    try:
        root.update()
        root.update_idletasks()
        assert root.register_button.winfo_manager() == "pack"
        assert root.register_button.cget("text") == "CADASTRAR VISITANTE"
        assert root.delivery_button.cget("text") == "Verificar última entrega"
        assert root.delivery_button.winfo_manager() == "pack"
        assert root.delivery_button.master is root.register_button.master
        assert (root.delivery_button.winfo_rootx() + root.delivery_button.winfo_width()
                <= root.register_button.winfo_rootx())
        assert root.register_button.winfo_rooty() + root.register_button.winfo_height() <= (
            root.winfo_rooty() + root.winfo_height())
        buttons = [c.cget("text") for c in root.registration_tab.winfo_children()
                   if hasattr(c, "cget") and "text" in c.keys()]
        assert "Sincronizar grupos e níveis" not in buttons
        assert "Concluir pendente" not in buttons
        root.ticket.set("2 dias")
        root.start.set("31/10/2099 20:45")
        root.update_idletasks()
        # Outside operating hours: move to 09:00 next day and close at 17:00
        # on the second day of the ticket (not 23:59 as in older versions).
        assert root.start.get() == "01/11/2099 09:00"
        assert root.end.get() == "02/11/2099 17:00"
        root.book.select(root.settings_tab)
        root.update()
        assert root.register_button.winfo_manager() == ""
        assert root.delivery_button.winfo_manager() == ""
        root.book.select(root.registration_tab)
        root.update()
        assert root.register_button.winfo_manager() == "pack"
        assert root.delivery_button.winfo_manager() == "pack"
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Teste Tk requer Windows.")
def test_settings_requires_password_and_locks_again(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    asked = []
    monkeypatch.setattr(app.simpledialog, "askstring",
                        lambda *a, **kw: asked.append(kw.get("show")) or "test-password")
    monkeypatch.setattr(app, "verify_settings_password", lambda candidate: candidate == "test-password")
    root = app.ValeApp()
    try:
        root.update()
        assert not root.groups_box["values"]  # Antes da consulta: nada hardcoded no combo.
        assert root.group.get() == ""
        root.book.select(root.settings_tab)
        root.update()
        assert asked == ["*"]
        assert root.book.select() == str(root.settings_tab)
        root.book.select(root.registration_tab)
        root.update()
        assert root._settings_unlocked is False
        root.book.select(root.settings_tab)
        root.update()
        assert asked == ["*", "*"]  # Requer senha ao acessar outra vez.
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Teste Tk requer Windows.")
def test_bad_settings_password_never_opens_tab(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.simpledialog, "askstring", lambda *a, **kw: "wrong")
    monkeypatch.setattr(app, "verify_settings_password", lambda candidate: False)
    warnings = []
    monkeypatch.setattr(app.messagebox, "showerror", lambda *a, **kw: warnings.append(a))
    root = app.ValeApp()
    try:
        root.update()
        root.book.select(root.settings_tab)
        root.update()
        assert root.book.select() == str(root.registration_tab)
        assert root._settings_unlocked is False
        assert len(warnings) == 1
    finally:
        root.destroy()


def test_hikcentral_groups_hide_only_generic_visitors():
    from app import selectable_visitor_groups
    source = ["Visitors", "Day use", "Hóspedes Vale", "Hóspedes Serra", "Visitors", "Guests", " visitors "]
    assert selectable_visitor_groups(source) == [
        "Day use", "Hóspedes Vale", "Hóspedes Serra", "Guests"
    ]
    assert selectable_visitor_groups(["Visitors", " visitors "]) == []
    assert selectable_visitor_groups(["Visitantes", "VISITOR", "Day use"]) == [
        "Visitantes", "VISITOR", "Day use"
    ]


@pytest.mark.skipif(os.name != "nt", reason="Interface Tk exige Windows.")
def test_preferred_group_select_only_after_api_and_manual_override(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    settings = default_settings()
    settings["preferred_group"] = "Hóspedes Vale"
    monkeypatch.setattr(app, "load_settings", lambda: settings)
    monkeypatch.setattr(app.simpledialog, "askstring", lambda *a, **kw: "test")
    monkeypatch.setattr(app, "verify_settings_password", lambda _: True)
    root = app.ValeApp()
    try:
        root.update()
        assert str(root.preferred_box.cget("state")) == "disabled"
        assert root.group.get() == ""
        assert not root.groups_box["values"]
        root._apply_live_groups(["Visitors", "Day use", "Hóspedes Serra", "Hóspedes Vale"])
        assert str(root.preferred_box.cget("state")) == "readonly"
        assert "Visitors" not in root.preferred_box["values"]
        assert tuple(root.groups_box["values"]) == ("Day use", "Hóspedes Serra", "Hóspedes Vale")
        assert root.s_preferred_group.get() == "Hóspedes Vale"
        assert root.group.get() == "Hóspedes Vale"
        # Preferência salva impõe bloqueio visual e na próxima sincronização.
        assert str(root.groups_box.cget("state")) == "disabled"
        root.group.set("Hóspedes Serra")
        assert root._apply_live_groups(["Hóspedes Vale", "Day use", "Hóspedes Serra"])
        assert root.group.get() == "Hóspedes Vale"
        # Se o grupo deixar de existir, não substituir por outro acesso.
        assert root._apply_live_groups(["Visitors", "Day use"]) is False
        assert root.group.get() == ""
        assert str(root.groups_box.cget("state")) == "disabled"
        assert root.s_preferred_group.get() == "Hóspedes Vale"
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Interface Tk exige Windows.")
def test_preferred_group_change_saves_and_is_reselected(monkeypatch, tmp_path):
    import app
    from core import default_settings, save_settings, load_settings
    original = default_settings()
    save_settings(original, tmp_path)
    monkeypatch.setattr(app, "load_settings", lambda: load_settings(tmp_path))
    monkeypatch.setattr(app, "load_api_keys", lambda: ("test-key", "test-secret"))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    saved_keys = []
    monkeypatch.setattr(app, "save_api_keys", lambda k, s: saved_keys.append((k, s)))
    monkeypatch.setattr(app, "save_settings", lambda value: save_settings(value, tmp_path))
    monkeypatch.setattr(app.simpledialog, "askstring", lambda *a, **kw: "test")
    monkeypatch.setattr(app, "verify_settings_password", lambda _: True)
    root = app.ValeApp()
    try:
        root.update()
        root._apply_live_groups(["Day use", "Hóspedes Vale", "Hóspedes Serra"])
        root.group.set("Day use")
        root.s_preferred_group.set("Hóspedes Serra")
        # Does not call the real API. Saving persists preference and updates next sync.
        assert root.save_config(silent=True) is True
        assert load_settings(tmp_path)["preferred_group"] == "Hóspedes Serra"
        root._apply_live_groups(["Day use", "Hóspedes Vale", "Hóspedes Serra"])
        assert root.group.get() == "Hóspedes Serra"
        assert root.s_preferred_group.get() == "Hóspedes Serra"
        # Escolha de grupo bloqueada não muda após nova sincronização.
        root.group.set("Day use")
        root._apply_live_groups(["Hóspedes Serra", "Day use"])
        assert root.group.get() == "Hóspedes Serra"
        assert str(root.groups_box.cget("state")) == "disabled"
        assert saved_keys
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Geometria requer Windows")
def test_modern_dashboard_keeps_all_original_actions_and_visible_settings_save(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.simpledialog, "askstring", lambda *a, **kw: "test-password")
    monkeypatch.setattr(app, "verify_settings_password", lambda candidate: candidate == "test-password")
    root = app.ValeApp()
    try:
        root.geometry("1020x700")
        root.update()
        assert root.nav_registration.winfo_exists()
        assert root.nav_settings.winfo_exists()
        assert root.register_button.winfo_manager() == "pack"
        assert root.delivery_button.winfo_manager() == "pack"
        assert root.connection_label_var.get() != "Conectado"
        assert root.first_entry.winfo_exists()
        assert str(root.groups_box.cget("state")) == "readonly"
        assert str(root.preferred_box.cget("state")) == "disabled"
        assert root.schedule_text.winfo_exists()
        assert "1 dia;1;09:00;17:00" in root.tickets_text.get("1.0", "end")
        assert root.book.select() == str(root.registration_tab)
        root.book.select(root.settings_tab)
        root.update()
        assert root.book.select() == str(root.settings_tab)
        assert root.register_button.winfo_manager() == ""
        assert root.delivery_button.winfo_manager() == ""
        assert root.save_button.winfo_manager() == "pack"
        save = root.save_button
        # The Settings action stays pinned above the bottom of a minimum-size window.
        assert save.winfo_rooty() > root.winfo_rooty()
        assert save.winfo_rooty() + save.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        root.book.select(root.registration_tab)
        root.update()
        assert root.register_button.winfo_manager() == "pack"
        assert root.delivery_button.winfo_manager() == "pack"
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Tk requer Windows")
def test_only_combo_is_visible_and_group_is_free_without_preference(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    root = app.ValeApp()
    try:
        root.update()
        assert not hasattr(root, "group_chips_frame")
        assert root._apply_live_groups(["Visitors", "Day use", "Hóspedes Vale"])
        assert root.group.get() == "Day use"
        assert str(root.groups_box.cget("state")) == "readonly"
        root.group.set("Hóspedes Vale")
        assert root.group.get() == "Hóspedes Vale"
        assert root._apply_live_groups(["Day use", "Hóspedes Vale"])
        assert root.group.get() == "Hóspedes Vale"
        assert tuple(root.groups_box["values"]) == ("Day use", "Hóspedes Vale")
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Tk requer Windows")
def test_responsive_ui_compact_screen_and_settings_footer(monkeypatch):
    import app
    import responsive_ui
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.simpledialog, "askstring", lambda *a, **kw: "test")
    monkeypatch.setattr(app, "verify_settings_password", lambda _: True)
    root = app.ValeApp()
    try:
        for size in (1366, 1024, 890):
            responsive_ui.apply(root, size)
            root.geometry(f"{size}x650")
            root.update()
            assert root.visitor_grid.winfo_exists()
            assert root.registration_footer.winfo_exists()
            assert root.registration_status_widget.winfo_manager() == "pack"
            assert root.register_button.winfo_manager() == "pack"
            assert root.delivery_button.winfo_manager() == "pack"
            assert root.register_button.winfo_rootx() + root.register_button.winfo_width() <= root.winfo_rootx() + root.winfo_width()
        root.book.select(root.settings_tab)
        root.update()
        assert root.save_button.winfo_manager() == "pack"
        assert root.save_button.winfo_rooty() + root.save_button.winfo_height() <= root.winfo_rooty() + root.winfo_height()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Validação de layout requer Windows e Tk")
def test_valid_times_setting_is_visible_saved_and_reopened_without_live_api(monkeypatch, tmp_path):
    import app
    from core import default_settings, save_settings, load_settings
    save_settings(default_settings(), tmp_path)
    monkeypatch.setattr(app, "load_settings", lambda: load_settings(tmp_path))
    monkeypatch.setattr(app, "load_api_keys", lambda: ("test-key", "test-secret"))
    monkeypatch.setattr(app, "save_api_keys", lambda key, secret: None)
    monkeypatch.setattr(app, "save_settings", lambda conf: save_settings(conf, tmp_path))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    try:
        root.update()
        assert root.s_valid_times.get() == "0"
        assert root.s_valid_times._name
        root.s_valid_times.set("7")
        assert root.save_config(silent=True)
        assert load_settings(tmp_path)["valid_times"] == 7
        root.s_valid_times.set("101")
        assert not root.save_config(silent=True)
        assert load_settings(tmp_path)["valid_times"] == 7
        root.s_valid_times.set("0")
        assert root.save_config(silent=True)
        assert load_settings(tmp_path)["valid_times"] == 0
        root._fill_settings()
        assert root.s_valid_times.get() == "0"
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Spinner precisa do Tk no Windows")
@pytest.mark.parametrize("fails", [False, True])
def test_registration_spinner_animates_only_during_confirmed_task_and_stops(monkeypatch, fails):
    import app
    import threading
    import time
    from core import default_settings

    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    release = threading.Event()
    outcomes = []
    try:
        root.update_idletasks()
        # Opening the registration screen must not display a loading spinner.
        assert not getattr(root, "_registration_loading_active", False)

        def work():
            if not release.wait(timeout=3):
                raise RuntimeError("Teste sem liberação")
            if fails:
                raise RuntimeError("Falha simulada do HikCentral")
            return {"registered": True}

        def finish(result, error):
            outcomes.append((result, error))

        root.task(work, finish, registration_loading=True)
        root.update_idletasks()
        assert root.busy
        assert root._registration_loading_active
        assert root.registration_loading_overlay.winfo_manager() == "place"
        assert root.register_button.cget("state") == "disabled"
        assert root.delivery_button.cget("state") == "disabled"
        assert root.nav_registration.cget("state") == "disabled"
        assert root.nav_settings.cget("state") == "disabled"
        initial_angle = root._registration_spinner_angle
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and root._registration_spinner_angle == initial_angle:
            root.update()
            time.sleep(0.01)
        assert root._registration_spinner_angle != initial_angle

        release.set()
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and not outcomes:
            root.update()
            time.sleep(0.01)
        assert len(outcomes) == 1
        result, error = outcomes[0]
        if fails:
            assert result is None
            assert isinstance(error, RuntimeError)
        else:
            assert result == {"registered": True}
            assert error is None
        assert not root.busy
        assert not root._registration_loading_active
        assert root.registration_loading_overlay.winfo_manager() == ""
        assert root._registration_spinner_job is None
        assert root.register_button.cget("state") == "normal"
        assert root.delivery_button.cget("state") == "normal"
        assert root.nav_registration.cget("state") == "normal"
        assert root.nav_settings.cget("state") == "normal"
    finally:
        release.set()
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Tk exige Windows")
def test_preflight_task_has_no_registration_spinner(monkeypatch):
    import app
    import threading
    import time
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    release = threading.Event()
    done = []
    try:
        def work():
            release.wait(timeout=3)
            return ("Visitor type=2", [])
        root.task(work, lambda value, error: done.append((value, error)))
        root.update_idletasks()
        assert root.busy
        assert not getattr(root, "_registration_loading_active", False)
        assert not hasattr(root, "registration_loading_overlay")
        release.set()
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and not done:
            root.update()
            time.sleep(0.01)
        assert len(done) == 1
    finally:
        release.set()
        root.destroy()
