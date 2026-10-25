"""Testa o layout do EXE Windows sem acesso ao HikCentral."""
import os
import pytest


@pytest.mark.skipif(os.name != "nt", reason="O teste de geometria exige Windows com Tk.")
def test_register_button_visible_and_form_is_simplified(monkeypatch):
    import app
    from core import default_settings
    monkeypatch.setattr(app, "load_settings", default_settings)
    monkeypatch.setattr(app, "load_api_keys", lambda: ("", ""))
    root = app.ValeApp()
    try:
        root.update()
        root.update_idletasks()
        assert root.register_button.winfo_manager() == "pack"
        assert root.register_button.cget("text") == "CADASTRAR VISITANTE"
        assert root.register_button.winfo_rooty() + root.register_button.winfo_height() <= (
            root.winfo_rooty() + root.winfo_height())
        buttons = [c.cget("text") for c in root.registration_tab.winfo_children()
                   if hasattr(c, "cget") and "text" in c.keys()]
        assert "Sincronizar grupos e níveis" not in buttons
        assert "Concluir pendente" not in buttons
        root.ticket.set("2 dias")
        root.start.set("31/10/2099 20:45")
        root.update_idletasks()
        assert root.end.get() == "01/11/2099 23:59"
        root.book.select(root.settings_tab)
        root.update()
        assert root.register_button.winfo_manager() == ""
        root.book.select(root.registration_tab)
        root.update()
        assert root.register_button.winfo_manager() == "pack"
    finally:
        root.destroy()
