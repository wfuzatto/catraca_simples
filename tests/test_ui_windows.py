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
        root.start.set("31/10/2099")
        root.update_idletasks()
        # O caixa recebe somente datas; horas ficam na área protegida.
        assert root.start.get() == "31/10/2099"
        assert root.end.get() == "01/11/2099"
        assert root.end_entry.cget("state") == "readonly"
        # O texto permanece atualizável pelo cálculo programático.
        root.start.set("07/11/2099")
        root.update_idletasks()
        assert root.end.get() == "08/11/2099"
        assert root.s_validity_start.get() == "00:00:00"
        assert root.s_validity_end.get() == "23:59:59"
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
        # Mesmo uma alteração programática da variável não muda o zero seguro.
        root.s_valid_times.set("7")
        assert root.save_config(silent=True)
        assert load_settings(tmp_path)["valid_times"] == 0
        assert root.s_valid_times.get() == "0"
        root.s_valid_times.set("101")
        assert root.save_config(silent=True)
        assert load_settings(tmp_path)["valid_times"] == 0
        root._fill_settings()
        assert root.s_valid_times.get() == "0"
        # Ao iniciar outro executável no mesmo perfil, as preferências são lidas.
        root.settings = load_settings(tmp_path)
        root._fill_settings()
        assert root.s_valid_times.get() == "0"
    finally:
        root.destroy()



@pytest.mark.skipif(os.name != "nt", reason="Spinner precisa do Tk no Windows")
@pytest.mark.parametrize("fails", [False, True])
def test_registration_spinner_animates_only_during_confirmed_task_and_stops(monkeypatch, fails):
    import app
    import threading
    from core import default_settings

    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    release = threading.Event()
    outcomes = []
    observed = []
    timed_out = []
    try:
        root.update_idletasks()
        assert not getattr(root, "_registration_loading_active", False)

        def work():
            if not release.wait(timeout=4):
                raise RuntimeError("Teste sem liberação")
            if fails:
                raise RuntimeError("Falha simulada do HikCentral")
            return {"registered": True}

        def finish(result, error):
            outcomes.append((result, error))
            root.quit()

        def inspect_while_working():
            # This callback runs on Tk's active main loop, just like production.
            observed.append({
                "busy": root.busy,
                "visible": root.registration_loading_overlay.winfo_viewable() == 1,
                "spinning": root._registration_spinner_angle > 0,
                "register_disabled": root.register_button.cget("state") == "disabled",
                "delivery_disabled": root.delivery_button.cget("state") == "disabled",
                "nav_disabled": root.nav_settings.cget("state") == "disabled",
            })

        def timeout():
            timed_out.append(True)
            release.set()
            root.quit()

        root.task(work, finish, registration_loading=True)
        assert root.busy
        assert root._registration_loading_active
        root.after(220, inspect_while_working)
        root.after(280, release.set)
        watchdog = root.after(3500, timeout)
        root.mainloop()  # Required for Tkinter's cross-thread after() dispatch.
        root.after_cancel(watchdog)
        assert not timed_out
        assert observed == [{
            "busy": True, "visible": True, "spinning": True,
            "register_disabled": True, "delivery_disabled": True,
            "nav_disabled": True,
        }]
        assert len(outcomes) == 1
        result, error = outcomes[0]
        if fails:
            assert result is None and isinstance(error, RuntimeError)
        else:
            assert result == {"registered": True} and error is None
        assert not root.busy
        assert not root._registration_loading_active
        assert root.registration_loading_overlay.state() == "withdrawn"
        assert root._registration_spinner_job is None
        for widget in (root.register_button, root.delivery_button,
                       root.nav_registration, root.nav_settings):
            assert widget.cget("state") == "normal"
    finally:
        release.set()
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Tk exige Windows")
def test_preflight_task_has_no_registration_spinner(monkeypatch):
    import app
    import threading
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    release = threading.Event()
    done = []
    timed_out = []
    try:
        def work():
            release.wait(timeout=3)
            return ("Visitor type=2", [])

        def finish(value, error):
            done.append((value, error))
            root.quit()

        def timeout():
            timed_out.append(True)
            release.set()
            root.quit()

        root.task(work, finish)
        assert root.busy
        assert not getattr(root, "_registration_loading_active", False)
        assert not hasattr(root, "registration_loading_overlay")
        root.after(100, release.set)
        watchdog = root.after(3000, timeout)
        root.mainloop()
        root.after_cancel(watchdog)
        assert not timed_out
        assert done == [(("Visitor type=2", []), None)]
    finally:
        release.set()
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Verificação da aba exige Windows Tk")
def test_new_history_tab_keeps_registration_and_settings_intact(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    monkeypatch.setattr(app, "list_history", lambda: [])
    root = app.ValeApp()
    try:
        root.update_idletasks()
        assert root.history_tab.winfo_exists()
        assert root.nav_history.winfo_exists()
        assert root.register_button.winfo_exists()
        assert root.save_button.winfo_exists()
        assert root.book.select() == str(root.registration_tab)
        root.book.select(root.history_tab)
        root.update()
        assert root.history_table.winfo_exists()
        assert "Atualizar lista" in root.history_status.get()
        assert root.register_button.winfo_manager() == ""
        root.book.select(root.registration_tab)
        root.update()
        assert root.register_button.winfo_manager() == "pack"
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Visualização real da foto exige Tk do Windows")
def test_remote_editor_shows_photo_at_top_and_blocks_unverified_updates(monkeypatch):
    """Teste de janela offline: nenhuma chamada ao HikCentral, nenhum cadastro alterado."""
    import io
    import customtkinter as ctk
    import app
    import visitor_editor
    from PIL import Image
    from core import default_settings

    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    monkeypatch.setattr(visitor_editor, "load_pending", lambda: None)
    root = app.ValeApp()
    try:
        root.update()
        stream = io.BytesIO()
        Image.new("RGB", (80, 80), "white").save(stream, "JPEG")
        record = {
            "visitor_id": "test-photo-812",
            "first": "Visitante", "last": "Teste",
            "group": "Hóspedes Serra", "reservation": "Apto 3",
            "entry_at": "2099-11-02T10:00:00-03:00",
            "exit_at": "2099-11-04T17:00:00-03:00",
            "origin": "HikCentral", "access_names": [],
        }
        visitor_editor.open_visitor_editor(
            root, record, initial_photo=stream.getvalue(), readonly=True,
            readonly_reason="Sem nível original verificado.")
        root.update_idletasks()
        dialogs = [child for child in root.winfo_children()
                   if isinstance(child, ctk.CTkToplevel)]
        assert len(dialogs) == 1
        dialog = dialogs[0]
        assert getattr(dialog._photo_preview_widget, "image", None) is not None
        assert dialog._save_edit_button.cget("state") == "disabled"
        dialog.destroy()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Layout da lista necessita Tk no Windows")
def test_history_table_has_validity_and_actual_passage_columns(monkeypatch):
    """Sem HikCentral: coluna de validade nunca substitui passagem física."""
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    try:
        root.book.select(root.history_tab)
        root.update()
        columns = tuple(root.history_table["columns"])
        assert columns == (
            "registered", "start", "end", "name", "group",
            "checkin", "first_entry", "last_exit")
        titles = [root.history_table.heading(col)["text"] for col in columns]
        assert titles == [
            "Cadastrado em", "Início válido", "Término válido",
            "Visitante", "Grupo", "Check-in", "Primeira entrada", "Última saída"]
        assert "Nível" not in titles and "Catracas" not in titles
        assert root.history_table.cget("xscrollcommand")

        root._show_history_rows([{
            "visitor_id": "123", "registered_at": "2026-11-03T10:00:00-03:00",
            "entry_at": "2026-11-03T09:00:00-03:00",
            "exit_at": "2026-11-05T17:00:00-03:00",
            "first": "Ana", "last": "Teste", "group": "Hóspedes Vale",
            "checkin_state": "check-in efetuado",
            "first_entry_at": "2026-11-03T10:15:00-03:00",
            "last_exit_at": "2026-11-03T16:15:00-03:00",
        }], "Teste offline")
        values = root.history_table.item("0")["values"]
        assert len(values) == 8
        assert values[2] == "05/11/2026 17:00:00"
        assert values[6] == "03/11/2026 10:15:00"
        assert values[7] == "03/11/2026 16:15:00"
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Indicador de progresso usa Tk do Windows")
def test_history_loading_indicator_for_list_and_editor(monkeypatch):
    """Progressbar visível, com fases simultâneas e retorno ao estado inicial."""
    import app
    import modern_ui
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    try:
        root.book.select(root.history_tab)
        root.update()
        assert root._history_ui_built
        assert not root.history_loading_frame.winfo_manager()
        modern_ui.start_history_loading(root, "Carregando cadastros...", phase="records")
        root.update()
        assert root.history_loading_frame.winfo_manager() == "pack"
        assert "Carregando cadastros" in root.history_loading_label.cget("text")
        modern_ui.start_history_loading(root, "Carregando foto e dados...", phase="editor")
        assert "Carregando foto" in root.history_loading_label.cget("text")
        modern_ui.stop_history_loading(root, phase="records")
        assert root.history_loading_frame.winfo_manager() == "pack"
        modern_ui.stop_history_loading(root, phase="editor")
        root.update()
        assert not root.history_loading_frame.winfo_manager()
        assert not root._history_progress_running
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Teste de layout exige interface Tk no Windows")
def test_history_has_no_redundant_buttons_and_opens_editor_by_double_click(monkeypatch):
    """Retirar apenas a linha de ações, mantendo busca, paginação e duplo clique."""
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    root = app.ValeApp()
    try:
        root.update()
        root.book.select(root.history_tab)
        root.update()
        assert root.history_table.bind("<Double-1>")
        assert "dois cliques" in root.history_double_click_hint.cget("text")
        for old_button in (
            "history_verify_button", "history_resend_button",
            "history_details_button", "history_photo_button"):
            assert not hasattr(root, old_button)
        assert root.history_table.winfo_exists()
        assert root.history_previous_button.winfo_exists()
        assert root.history_next_button.winfo_exists()
        assert root.history_loading_frame.winfo_exists()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Teste do editor exige Tk Windows")
def test_general_visitor_without_checkin_opens_photo_readonly(monkeypatch):
    """Visitor Information não possui datas de visita nem recordId: nunca editar."""
    import app
    import visitor_editor
    import customtkinter as ctk
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    monkeypatch.setattr(visitor_editor, "load_pending", lambda: None)
    root = app.ValeApp()
    try:
        root.update()
        record = {
            "visitor_id": "test-directory",
            "first": "FABRICIO", "last": "LEMOS",
            "full_name": "FABRICIO LEMOS",
            "group": "Hóspedes Vale", "reservation": "",
            "entry_at": "", "exit_at": "",
            "access_names": [], "origin": "VisitorInformation",
        }
        visitor_editor.open_visitor_editor(
            root, record, readonly=True,
            readonly_reason="Visitante sem check-in no período.")
        root.update_idletasks()
        windows = [child for child in root.winfo_children()
                   if isinstance(child, ctk.CTkToplevel)]
        assert len(windows) == 1
        assert windows[0]._save_edit_button.cget("state") == "disabled"
        windows[0].destroy()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Teste da janela de pendências exige Windows")
def test_pending_recovery_button_and_readonly_editor(monkeypatch, tmp_path):
    import app
    import core
    import visitor_editor
    import customtkinter as ctk
    from core import default_settings
    (tmp_path / "pending.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(core, "app_dir", lambda: tmp_path)
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    marker = {"stage": "appointment_inflight"}
    monkeypatch.setattr(app, "load_pending", lambda: marker)
    monkeypatch.setattr(visitor_editor, "load_pending", lambda: marker)
    pending = {
        "stage": "appointment_inflight",
        "description": "Reserva com resposta incerta. Não reenviar automaticamente.",
        "visitor_id": "", "appointment_id": "", "registration_record_id": "",
        "registered": False, "entry_at": "", "exit_at": "", "access_names": [],
        "door_count": 0, "assigned_count": 0,
        "modified_at": "03/11/2026 15:00:00",
        "can_resume": False, "sha256": "a" * 64,
    }
    monkeypatch.setattr(app, "read_pending_diagnostics", lambda: pending)
    root = app.ValeApp()
    try:
        root.update()
        assert root.delivery_button.cget("text") == "Resolver pendência"
        root.show_pending_recovery()
        root.update_idletasks()
        dialogs = [x for x in root.winfo_children() if isinstance(x, ctk.CTkToplevel)]
        assert len(dialogs) == 1
        dialogs[0].destroy()
        record = {
            "visitor_id": "visitor-test", "first": "Maria", "last": "Teste",
            "group": "Hóspedes Vale", "entry_at": "2099-11-02T09:00:00-03:00",
            "exit_at": "2099-11-02T17:00:00-03:00", "origin": "HikCentral",
            "access_names": [],
        }
        visitor_editor.open_visitor_editor(root, record, readonly=False)
        root.update_idletasks()
        dialogs = [x for x in root.winfo_children() if isinstance(x, ctk.CTkToplevel)]
        assert len(dialogs) == 1
        assert dialogs[0]._save_edit_button.cget("state") == "disabled"
        assert dialogs[0].title().startswith("Visualizar cadastro")
        dialogs[0].destroy()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Teste do editor exige Tk Windows")
def test_editor_allows_replacement_photo_when_download_fails(monkeypatch):
    """HTTP 200/código 128 não deve bloquear seleção ou captura de nova foto."""
    import app
    import visitor_editor
    import customtkinter as ctk
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    monkeypatch.setattr(visitor_editor, "load_pending", lambda: None)
    root = app.ValeApp()
    try:
        root.update()
        record = {
            "visitor_id": "1009",
            "registration_record_id": "123",
            "first": "Teste", "last": "Hóspede",
            "group": "Hóspedes Vale",
            "entry_at": "2099-11-03T12:20:00-03:00",
            "exit_at": "2099-11-03T17:00:00-03:00",
            "origin": "HikCentral",
            "access_names": ["VISITANTE_VALE"],
            "door_ids": ["A", "B", "C"],
            "_requires_access_confirmation": True,
            "_original_appointment_id": "appt-1009",
            "photo_url": "opaque-photo-ref",
            "card_number": "123123123",
        }
        visitor_editor.open_visitor_editor(
            root, record,
            remote_details={"visitorId": "1009", "visitorGroupName": "Hóspedes Vale"},
            initial_photo=b"", photo_error="HikCentral código 128")
        root.update_idletasks()
        dialogs = [x for x in root.winfo_children() if isinstance(x, ctk.CTkToplevel)]
        assert len(dialogs) == 1
        popup = dialogs[0]
        assert popup.title().startswith("Editar cadastro")
        assert popup._save_edit_button.cget("state") == "normal"
        assert popup._photo_preview_widget.winfo_exists()
        popup.destroy()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Interface Tk do editor exige Windows")
def test_editor_preserves_original_group_and_blocks_direct_reassignment(monkeypatch):
    import app
    import visitor_editor
    import customtkinter as ctk
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    monkeypatch.setattr(visitor_editor, "load_pending", lambda: None)
    root = app.ValeApp()
    try:
        root.connected_groups = ["Hóspedes Vale", "Hóspedes Serra", "Day use"]
        root.update()
        record = {
            "visitor_id": "1009", "registration_record_id": "check-1009",
            "first": "Wesley", "last": "Teste", "group": "Hóspedes Vale",
            "entry_at": "2099-11-03T12:20:00-03:00",
            "exit_at": "2099-11-03T17:00:00-03:00",
            "origin": "HikCentral", "access_names": ["VISITANTE_VALE"],
            "door_ids": ["A", "B", "C"], "card_number": "123123123",
            "_requires_access_confirmation": True,
            "_original_appointment_id": "appt-1009",
        }
        visitor_editor.open_visitor_editor(
            root, record, remote_details={"visitorId": "1009", "cards": [
                {"cardNo": "123123123"}]})
        root.update_idletasks()
        popup = next(x for x in root.winfo_children()
                     if isinstance(x, ctk.CTkToplevel))
        combo = popup._editor_group_selector
        assert combo.instate(["disabled"])
        assert combo.get() == "Hóspedes Vale"
        # A edição da validade NÃO deve listar Serra como alternativa.
        assert list(combo.cget("values")) == ["Hóspedes Vale"]
        assert popup._save_edit_button.cget("state") == "normal"
        visitor_editor.open_visitor_editor(
            root, record, remote_details={"visitorId": "1009"})
        assert len([x for x in root.winfo_children()
                    if isinstance(x, ctk.CTkToplevel) and x.winfo_exists()]) == 1
        popup.destroy()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Tk Windows UI")
def test_history_is_lazy_and_never_autofetches_on_tab_change(monkeypatch):
    """Building or revisiting History must not contact HikCentral/read DPAPI."""
    import app
    import modern_ui
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    monkeypatch.setattr(app, "list_history", lambda: (
        pytest.fail("Historical DPAPI read should be explicit")))
    monkeypatch.setattr(app, "fetch_checkins", lambda *a, **kw: (
        pytest.fail("Remote history should not be fetched automatically")))
    root = app.ValeApp()
    try:
        assert root._history_ui_built is False
        assert not hasattr(root, "history_table")
        root.book.select(root.history_tab)
        root.update()
        assert root._history_ui_built is True
        tree = root.history_table
        assert "Atualizar lista" in root.history_status.get()
        root.book.select(root.registration_tab)
        root.update()
        root.book.select(root.history_tab)
        root.update()
        assert root.history_table is tree
        assert root.history_tab.winfo_children().__len__() == 1
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Tk Windows")
def test_history_today_default_and_page_size_selection_do_not_autofetch(monkeypatch):
    import app
    from datetime import datetime
    from core import TZ, default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    monkeypatch.setattr(app.ValeApp, "_startup_sync", lambda self: None)
    monkeypatch.setattr(app.ValeApp, "refresh_history",
                        lambda self: pytest.fail("Consulta não pode ser automática."))
    root = app.ValeApp()
    try:
        assert not root._history_ui_built
        root.book.select(root.history_tab)
        root.update()
        today = datetime.now(TZ).strftime("%d/%m/%Y")
        assert root.history_start.get() == today
        assert root.history_end.get() == today
        assert root.history_page_size.get() == "10"
        assert tuple(root.history_page_size_box["values"]) == (
            "10", "50", "100", "200", "500")
        for size in ("50", "100", "200", "500", "10"):
            root.history_page_size.set(size)
            root.history_page_size_changed()
            assert root._history_page == 1
            assert "Atualizar lista" in root.history_status.get()
    finally:
        root.destroy()


@pytest.mark.skipif(os.name != "nt", reason="Tk e validação visual exigem Windows.")
def test_new_registration_date_fields_do_not_include_or_autofill_clock(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    root = app.ValeApp()
    try:
        root.update()
        assert len(root.start.get()) == 10
        assert len(root.end.get()) == 10
        assert ":" not in root.start.get()
        root.ticket.set("3 dias")
        root.start.set("05/11/2099")
        root.update_idletasks()
        assert root.start.get() == "05/11/2099"
        assert root.end.get() == "07/11/2099"
        # Clicar no formulário de configuração não modifica as datas.
        root.book.select(root.settings_tab)
        root.update_idletasks()
        assert root.s_validity_start.get() == "00:00:00"
        assert root.s_validity_end.get() == "23:59:59"
        root.book.select(root.registration_tab)
        root.update_idletasks()
        assert root.start.get() == "05/11/2099"
        assert root.end.get() == "07/11/2099"
    finally:
        root.destroy()

@pytest.mark.skipif(os.name != "nt", reason="Tk somente no Windows.")
def test_next_guest_reset_follows_success_and_exit_stays_readonly(monkeypatch):
    from datetime import datetime
    import app
    from core import TZ, default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    root = app.ValeApp()
    try:
        root.update()
        root.ticket.set("2 dias")
        root.start.set("07/11/2099")
        root.update_idletasks()
        assert root.end.get() == "08/11/2099"
        assert root.end_entry.cget("state") == "readonly"
        root._reset_registration_period_after_success(
            datetime(2026, 11, 5, 21, 9, tzinfo=TZ))
        root.update_idletasks()
        assert root.ticket.get() == "1 dia"
        assert root.start.get() == "05/11/2026"
        assert root.end.get() == "05/11/2026"
        assert root.end_entry.cget("state") == "readonly"
    finally:
        root.destroy()
