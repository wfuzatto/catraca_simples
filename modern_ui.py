"""Modern visual shell for Vale visitor registration (presentation only).

All operations remain implemented by app.ValeApp: OpenAPI, NFC, webcam,
registration, authorization, TLS, credential storage and recovery.
"""
from __future__ import annotations

import tkinter as tk
from datetime import datetime, timedelta
from core import TZ
from tkinter import ttk
import customtkinter as ctk
import responsive_ui

NAVY = "#14263B"
NAVY_2 = "#1B324A"
BG = "#F3F6FB"
WHITE = "#FFFFFF"
BLUE = "#1763D5"
BLUE_HOVER = "#1053B9"
INK = "#182841"
MUTED = "#687A95"
LINE = "#E3EAF4"
CYAN = "#22B9C6"
GREEN = "#20B978"


def _text(parent, value, *, size=13, color=INK, bold=False, **grid):
    widget = ctk.CTkLabel(
        parent, text=value, font=("Segoe UI", size, "bold" if bold else "normal"),
        text_color=color, anchor="w", justify="left")
    if grid:
        widget.grid(**grid)
    return widget


def _card(parent, *, padx=20, pady=18):
    frame = ctk.CTkFrame(
        parent, fg_color=WHITE, corner_radius=16, border_width=1, border_color=LINE)
    frame.pack(fill="x", pady=(0, 13))
    inner = ctk.CTkFrame(frame, fg_color="transparent")
    inner.pack(fill="both", expand=True, padx=padx, pady=pady)
    return inner


def _section(parent, number, title, subtitle, symbol):
    title_row = ctk.CTkFrame(parent, fg_color="transparent")
    title_row.pack(fill="x", pady=(0, 14))
    marker = ctk.CTkFrame(title_row, width=45, height=45, corner_radius=14, fg_color="#E8F1FE")
    marker.pack(side="left", padx=(0, 12))
    marker.pack_propagate(False)
    ctk.CTkLabel(marker, text=symbol, font=("Segoe UI Symbol", 22, "bold"),
                 text_color=BLUE).place(relx=.5, rely=.5, anchor="center")
    labels = ctk.CTkFrame(title_row, fg_color="transparent")
    labels.pack(side="left", fill="x", expand=True)
    _text(labels, f"{number}. {title}", size=18, bold=True).pack(anchor="w")
    _text(labels, subtitle, size=12, color=MUTED).pack(anchor="w", pady=(2, 0))


def _field(parent, label, variable, *, required=False, placeholder="", show=None):
    _text(parent, label + ("  *" if required else ""),
          size=12, bold=True).pack(anchor="w", pady=(0, 6))
    widget = ctk.CTkEntry(
        parent, textvariable=variable, height=42, corner_radius=8,
        border_width=1, border_color="#D5DFEB", fg_color=WHITE,
        text_color=INK, placeholder_text=placeholder, show=show,
        font=("Segoe UI", 13))
    widget.pack(fill="x")
    return widget


def _combo(parent, label, variable, *, required=False, width=27, state="readonly"):
    _text(parent, label + ("  *" if required else ""), size=12, bold=True).pack(anchor="w", pady=(0, 6))
    widget = ttk.Combobox(
        parent, textvariable=variable, state=state, width=width,
        style="Vale.TCombobox", font=("Segoe UI", 12))
    widget.pack(fill="x", ipady=7)
    return widget


def _primary(parent, label, command, *, width=180, **kwargs):
    return ctk.CTkButton(
        parent, text=label, command=command, width=width, height=43,
        fg_color=BLUE, hover_color=BLUE_HOVER,
        corner_radius=9, text_color=WHITE,
        font=("Segoe UI", 13, "bold"), **kwargs)


def _secondary(parent, label, command, *, width=170):
    return ctk.CTkButton(
        parent, text=label, command=command, width=width, height=43,
        corner_radius=9, fg_color="#F7FAFE", hover_color="#E5EFFC",
        border_width=1, border_color="#CFDAEA", text_color=NAVY_2,
        font=("Segoe UI", 12, "normal"))


def _nav_button(app, parent, name, symbol, target):
    outer = ctk.CTkButton(
        parent, text="   " + symbol + "    " + name, height=46,
        corner_radius=10, anchor="w", border_spacing=9, width=182,
        fg_color=BLUE if name == "Cadastro" else "transparent",
        hover_color="#25435F", text_color=WHITE if name == "Cadastro" else "#DBE7F6",
        font=("Segoe UI", 14), command=lambda: app.book.select(target))
    outer.pack(fill="x", padx=13, pady=4)
    return outer


def _summary_line(parent, label, variable, *, fg=MUTED):
    line = ctk.CTkFrame(parent, fg_color="transparent")
    line.pack(fill="x", pady=6)
    _text(line, label, size=12, color=MUTED).pack(side="left")
    ctk.CTkLabel(
        line, textvariable=variable, text_color=fg,
        font=("Segoe UI", 12, "bold"), anchor="e"
    ).pack(side="right")
    ctk.CTkFrame(parent, height=1, fg_color="#EDF1F6").pack(fill="x")



def _tick_registration_spinner(app):
    """Animate on the Tk main thread; never block HikCentral worker requests."""
    if not getattr(app, "_registration_loading_active", False):
        return
    canvas = app.registration_spinner_canvas
    # An open arc rotates smoothly to indicate unknown completion time.
    angle = (getattr(app, "_registration_spinner_angle", 0) + 24) % 360
    app._registration_spinner_angle = angle
    canvas.itemconfigure(app.registration_spinner_arc, start=angle)
    app._registration_spinner_job = app.after(65, lambda: _tick_registration_spinner(app))


def start_registration_loading(app):
    """Visible, independent progress window above the entire Tk/ttk notebook."""
    if getattr(app, "_registration_loading_active", False):
        return
    if not hasattr(app, "registration_loading_overlay") or not app.registration_loading_overlay.winfo_exists():
        # A widget placed inside the ttk.Notebook could be obscured by page
        # content on Windows. An owned Toplevel paints above the main window.
        overlay = ctk.CTkToplevel(app)
        overlay.withdraw()
        overlay.title("Cadastro em andamento")
        overlay.resizable(False, False)
        overlay.transient(app)
        overlay.protocol("WM_DELETE_WINDOW", lambda: None)
        overlay.configure(fg_color=WHITE)
        frame = ctk.CTkFrame(
            overlay, corner_radius=14, fg_color=WHITE,
            border_width=2, border_color="#C9D9F0")
        frame.pack(fill="both", expand=True, padx=1, pady=1)
        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(fill="both", expand=True, padx=20, pady=23)
        canvas = tk.Canvas(
            row, width=46, height=46, bg=WHITE, highlightthickness=0, borderwidth=0)
        canvas.pack(side="left", padx=(0, 16))
        canvas.create_oval(6, 6, 40, 40, outline="#E4ECF6", width=4)
        app.registration_spinner_arc = canvas.create_arc(
            6, 6, 40, 40, start=0, extent=250,
            outline=BLUE, width=4, style=tk.ARC)
        app.registration_spinner_canvas = canvas
        message = ctk.CTkFrame(row, fg_color="transparent")
        message.pack(side="left", fill="both", expand=True)
        _text(message, "Cadastrando visitante...", size=16, bold=True).pack(
            anchor="w", pady=(2, 5))
        _text(message, "Aguardando resposta do HikCentral.",
              size=12, color=MUTED).pack(anchor="w")
        app.registration_loading_overlay = overlay

    overlay = app.registration_loading_overlay
    app._registration_loading_active = True
    app._registration_spinner_angle = 0
    app.update_idletasks()
    width, height = 396, 134
    x = app.winfo_rootx() + max(0, (app.winfo_width() - width) // 2)
    y = app.winfo_rooty() + max(0, (app.winfo_height() - height) // 2)
    overlay.geometry(f"{width}x{height}+{x}+{y}")
    overlay.deiconify()
    overlay.lift(app)
    app.register_button.configure(state="disabled")
    app.delivery_button.configure(state="disabled")
    app.nav_registration.configure(state="disabled")
    app.nav_settings.configure(state="disabled")
    app.nav_history.configure(state="disabled")
    _tick_registration_spinner(app)
    overlay.update_idletasks()


def stop_registration_loading(app):
    """Close the loading window before reporting success or a HikCentral error."""
    app._registration_loading_active = False
    job = getattr(app, "_registration_spinner_job", None)
    app._registration_spinner_job = None
    if job is not None:
        try:
            app.after_cancel(job)
        except tk.TclError:
            pass
    overlay = getattr(app, "registration_loading_overlay", None)
    if overlay is not None and overlay.winfo_exists():
        overlay.withdraw()
    for name in ("register_button", "delivery_button", "nav_registration", "nav_history", "nav_settings"):
        widget = getattr(app, name, None)
        if widget is not None and widget.winfo_exists():
            widget.configure(state="normal")


def _registration(app):
    page = app.registration_tab
    page.configure(style="Vale.Page.TFrame")
    outer = ctk.CTkFrame(page, fg_color=BG, corner_radius=0)
    outer.pack(fill="both", expand=True)

    footer = ctk.CTkFrame(page, fg_color=WHITE, height=73, corner_radius=0,
                          border_width=1, border_color=LINE)
    footer.pack(side="bottom", fill="x")
    footer.pack_propagate(False)
    app.register_button = _primary(
        footer, "Cadastrar visitante", app.register, width=196)
    # Tests and operators expect these two actions together at right.
    app.register_button.configure(text="CADASTRAR VISITANTE")
    app.register_button.pack(side="right", padx=(8, 18), pady=13)
    app.delivery_button = _secondary(
        footer, "Verificar última entrega", app.verify_last_delivery, width=187)
    app.delivery_button.pack(side="right", padx=(8, 0), pady=13)
    app.registration_footer = footer
    app.registration_status_widget = ctk.CTkLabel(
        footer, textvariable=app.status, anchor="w",
        text_color=MUTED, font=("Segoe UI", 11), width=250,
        wraplength=370)
    app.registration_status_widget.pack(side="left", fill="x", expand=True, padx=18)

    # tk/ctk packing order: footer anchored to bottom, content fills remaining space.
    content = ctk.CTkFrame(outer, fg_color=BG, corner_radius=0)
    content.pack(fill="both", expand=True, padx=14, pady=11)
    # Única coluna: formulário ocupa toda a área útil; rolagem permanece.
    content.grid_columnconfigure(0, weight=1)
    content.grid_rowconfigure(0, weight=1)
    left = ctk.CTkScrollableFrame(
        content, fg_color=BG, corner_radius=0,
        scrollbar_button_color="#CBD8E8", scrollbar_button_hover_color="#ACBDD5")
    left.grid(row=0, column=0, sticky="nsew")

    # 1. Visitor identity.
    details = _card(left)
    _section(details, 1, "Dados do visitante", "Informe as informações do visitante", "●")
    columns = ctk.CTkFrame(details, fg_color="transparent")
    columns.pack(fill="x")
    columns.grid_columnconfigure((0, 1), weight=1, uniform="visitor")
    app.visitor_grid = columns
    app.first, app.last, app.group, app.reservation = (
        tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar())
    name_area = ctk.CTkFrame(columns, fg_color="transparent")
    name_area.grid(row=0, column=0, sticky="ew", padx=(0, 10), pady=(0, 12))
    app.first_entry = _field(name_area, "Nome", app.first, required=True, placeholder="Digite o nome")
    surname_area = ctk.CTkFrame(columns, fg_color="transparent")
    surname_area.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=(0, 12))
    _field(surname_area, "Sobrenome", app.last, required=True, placeholder="Digite o sobrenome")
    group_area = ctk.CTkFrame(columns, fg_color="transparent")
    group_area.grid(row=1, column=0, sticky="ew", padx=(0, 10))
    app.groups_box = _combo(group_area, "Grupo de visitante", app.group, required=True)
    app.groups_box["values"] = ()
    reservation_area = ctk.CTkFrame(columns, fg_color="transparent")
    reservation_area.grid(row=1, column=1, sticky="ew", padx=(10, 0))
    _field(reservation_area, "Reserva ou apartamento (opcional)", app.reservation,
           placeholder="Ex.: Reserva 101, apto 23")
    app.visitor_cells = [name_area, surname_area, group_area, reservation_area]

    # 2. Credentials.
    credentials = _card(left)
    _section(credentials, 2, "Credenciais de acesso",
             "Reconhecimento facial e/ou pulseira NFC", "◆")
    creds_row = ctk.CTkFrame(credentials, fg_color="transparent")
    creds_row.pack(fill="x")
    creds_row.grid_columnconfigure((0, 1), weight=1, uniform="credentials")
    app.credential_grid = creds_row
    nfc_panel = ctk.CTkFrame(creds_row, fg_color="#ECF9F5", corner_radius=12)
    nfc_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
    _text(nfc_panel, "▣   Cartão / Pulseira NFC", size=14, bold=True).pack(anchor="w", padx=13, pady=(14, 7))
    app.card = tk.StringVar()
    ctk.CTkEntry(
        nfc_panel, textvariable=app.card, height=42,
        corner_radius=8, border_width=1, border_color="#D1DDD9",
        fg_color=WHITE, text_color=INK,
        font=("Segoe UI", 14), placeholder_text="Número do cartão ou pulseira"
    ).pack(fill="x", padx=13)
    _secondary(nfc_panel, "◎  Ler ACR122U (PC/SC)", app.scan).pack(
        fill="x", padx=13, pady=(9, 14))
    app.uid = tk.StringVar(value="UID não lido")

    photo_panel = ctk.CTkFrame(creds_row, fg_color="#EDF5FD", corner_radius=12)
    photo_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
    _text(photo_panel, "▣   Foto facial", size=14, bold=True).pack(anchor="w", padx=13, pady=(14, 7))
    photo_inner = ctk.CTkFrame(photo_panel, fg_color="transparent")
    photo_inner.pack(fill="both", expand=True, padx=13, pady=(0, 14))
    photo_holder = ctk.CTkFrame(
        photo_inner, fg_color="#F7FAFE", width=150, height=135,
        corner_radius=11, border_width=1, border_color="#DAE3EF")
    photo_holder.pack(side="left", padx=(0, 10))
    photo_holder.pack_propagate(False)
    app.photo_widget = tk.Label(
        photo_holder, text="SEM FOTO", bg="#F7FAFE", fg=MUTED,
        borderwidth=0, highlightthickness=0, font=("Segoe UI", 10))
    # Preenchimento real do retângulo; PIL ImageOps.fit faz o crop proporcional.
    app.photo_widget.place(relx=0, rely=0, relwidth=1, relheight=1)
    choices = ctk.CTkFrame(photo_inner, fg_color="transparent")
    choices.pack(side="left", fill="both", expand=True)
    _primary(choices, "Escolher foto facial", app.browse_photo, width=174).pack(
        fill="x", pady=(7, 8))
    _secondary(choices, "Foto pela webcam", app.camera, width=174).pack(fill="x")
    app.credential_cells = [nfc_panel, photo_panel]

    # 3. Ticket.
    period = _card(left)
    _section(period, 3, "Ingresso e validade",
             "Defina o tipo de ingresso e o período de acesso", "▦")
    ticket_row = ctk.CTkFrame(period, fg_color="transparent")
    ticket_row.pack(fill="x")
    ticket_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="validity")
    app.ticket_grid = ticket_row
    app.ticket, app.start, app.end = tk.StringVar(), tk.StringVar(), tk.StringVar()
    ticket_area = ctk.CTkFrame(ticket_row, fg_color="transparent")
    ticket_area.grid(row=0, column=0, sticky="ew", padx=(0, 9))
    app.ticket_box = _combo(ticket_area, "Ingresso", app.ticket, required=True)
    app.ticket_box.bind("<<ComboboxSelected>>", lambda _: app.calculate())
    start_area = ctk.CTkFrame(ticket_row, fg_color="transparent")
    start_area.grid(row=0, column=1, sticky="ew", padx=9)
    _field(start_area, "Entrada", app.start, required=True)
    end_area = ctk.CTkFrame(ticket_row, fg_color="transparent")
    end_area.grid(row=0, column=2, sticky="ew", padx=(9, 0))
    _field(end_area, "Saída", app.end, required=True)
    app.ticket_cells = [ticket_area, start_area, end_area]
    app.start.trace_add("write", lambda *_: app.calculate())
    _text(period, "Horários diários do ingresso; bloqueios noturnos dependem da agenda dos níveis Visitor no HikCentral.",
          color=MUTED, size=11).pack(anchor="w", pady=(10, 0))


def _setting_field(parent, row, column, title, variable, *, secret=False):
    cell = ctk.CTkFrame(parent, fg_color="transparent")
    cell.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 12,
                                                         12 if column == 0 else 0), pady=8)
    _field(cell, title, variable, show="*" if secret else None)
    return cell



def _history(app):
    """Central check-ins from HikCentral, with explicit local offline fallback."""
    page = app.history_tab
    page.configure(style="Vale.Page.TFrame")
    background = ctk.CTkFrame(page, fg_color=BG, corner_radius=0)
    background.pack(fill="both", expand=True)
    inner = ctk.CTkFrame(background, fg_color=WHITE, corner_radius=14)
    inner.pack(fill="both", expand=True, padx=18, pady=18)
    heading = ctk.CTkFrame(inner, fg_color="transparent")
    heading.pack(fill="x", padx=17, pady=(18, 10))
    _text(heading, "Cadastros efetuados", size=19, bold=True).pack(side="left")
    _secondary(heading, "Atualizar lista", app.refresh_history, width=142).pack(side="right")
    _text(inner,
          "Cadastrado em = horário real disponível no histórico; Início válido = validade do ingresso. "
          "Consulta central HikCentral e fotos sob demanda; edição remota exige validar acessos originais.",
          size=11, color=MUTED).pack(anchor="w", padx=17, pady=(0, 7))
    today = datetime.now(TZ)
    app.history_start = tk.StringVar(value=(today-timedelta(days=30)).strftime("%d/%m/%Y"))
    app.history_end = tk.StringVar(value=today.strftime("%d/%m/%Y"))
    app.history_name = tk.StringVar()
    filters = ctk.CTkFrame(inner, fg_color="transparent")
    filters.pack(fill="x", padx=17, pady=(0, 8))
    ctk.CTkLabel(filters, text="De", text_color=INK).pack(side="left", padx=(0, 4))
    ctk.CTkEntry(filters, textvariable=app.history_start, width=100).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(filters, text="Até", text_color=INK).pack(side="left", padx=(0, 4))
    ctk.CTkEntry(filters, textvariable=app.history_end, width=100).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(filters, text="Nome", text_color=INK).pack(side="left", padx=(0, 4))
    ctk.CTkEntry(filters, textvariable=app.history_name, width=160).pack(side="left", padx=(0, 8))
    _secondary(filters, "Buscar", app.history_apply_filters, width=90).pack(side="left")
    table_area = ctk.CTkFrame(inner, fg_color="transparent")
    table_area.pack(fill="both", expand=True, padx=17)
    fields = ("registered", "date", "name", "group", "level", "checkin", "delivery")
    app.history_table = ttk.Treeview(table_area, columns=fields, show="headings", selectmode="browse")
    for key, label, width in (
        ("registered", "Cadastrado em", 135), ("date", "Início válido", 135),
        ("name", "Visitante", 180), ("group", "Grupo", 125),
        ("level", "Nível", 145), ("checkin", "Check-in", 120),
        ("delivery", "Catracas", 115)):
        app.history_table.heading(key, text=label)
        app.history_table.column(key, width=width, minwidth=90)
    app.history_table.pack(side="left", fill="both", expand=True)
    scroll = ttk.Scrollbar(table_area, orient="vertical", command=app.history_table.yview)
    scroll.pack(side="right", fill="y")
    app.history_table.configure(yscrollcommand=scroll.set)
    app.history_table.bind("<<TreeviewSelect>>", app._history_selection)
    app.history_table.bind("<Double-1>", app.view_history_selection)
    paging = ctk.CTkFrame(inner, fg_color="transparent")
    paging.pack(fill="x", padx=18, pady=(5, 0))
    app.history_previous_button = _secondary(
        paging, "Anterior", lambda: app.history_change_page(-1), width=95)
    app.history_previous_button.pack(side="left", padx=(0, 7))
    app.history_next_button = _secondary(
        paging, "Próxima", lambda: app.history_change_page(1), width=95)
    app.history_next_button.pack(side="left", padx=(0, 8))
    app.history_page_label = tk.StringVar(value="Página 1")
    ctk.CTkLabel(paging, textvariable=app.history_page_label, text_color=MUTED).pack(side="left")
    app.history_status = tk.StringVar(value="Selecione um cadastro para consultar.")
    ctk.CTkLabel(inner, textvariable=app.history_status, text_color=MUTED,
                 anchor="w", font=("Segoe UI", 12), wraplength=780).pack(
                     fill="x", padx=18, pady=(4, 10))
    actions = ctk.CTkFrame(inner, fg_color="transparent")
    actions.pack(fill="x", padx=18, pady=(0, 18))
    app.history_verify_button = _secondary(
        actions, "Consultar catracas", app.verify_history_selection, width=170)
    app.history_verify_button.pack(side="left", padx=(0, 9))
    app.history_resend_button = _primary(
        actions, "Reenviar credencial", app.resend_history_selection, width=182)
    app.history_resend_button.pack(side="left", padx=(0, 9))
    app.history_details_button = _secondary(
        actions, "Ver dados / corrigir", app.view_history_selection, width=160)
    app.history_details_button.pack(side="left", padx=(0, 9))
    app.history_photo_button = _secondary(
        actions, "Ver foto (HikCentral)", app.show_history_photo, width=160)
    app.history_photo_button.pack(side="left")


def _settings(app):
    page = app.settings_tab
    page.configure(style="Vale.Page.TFrame")
    wrapper = ctk.CTkFrame(page, fg_color=BG, corner_radius=0)
    wrapper.pack(fill="both", expand=True)
    # Persisted settings are never saved by changing a field; action is explicit.
    actions = ctk.CTkFrame(wrapper, fg_color=WHITE, height=75,
                           corner_radius=0, border_width=1, border_color=LINE)
    actions.pack(side="bottom", fill="x")
    actions.pack_propagate(False)
    app.save_button = _primary(
        actions, "Salvar configurações", app.save_config, width=192)
    app.save_button.pack(side="right", padx=(9, 18), pady=13)
    app.settings_test_button = _secondary(actions, "Testar conexão", app.connect, width=150)
    app.settings_test_button.pack(side="right", padx=(9, 0), pady=13)
    app.settings_diagnostic_button = _secondary(
        actions, "Diagnóstico de rede", app.diagnose, width=164)
    app.settings_diagnostic_button.pack(side="right", padx=(9, 0), pady=13)
    app.settings_notice = _text(actions,
        "Alterações só são gravadas ao clicar em Salvar.", size=11, color=MUTED)
    app.settings_notice.pack(side="left", padx=18)
    app.settings_footer = actions

    scroller = ctk.CTkScrollableFrame(
        wrapper, fg_color=BG, corner_radius=0,
        scrollbar_button_color="#CFDAEB")
    scroller.pack(fill="both", expand=True, padx=18, pady=13)

    app.s_host, app.s_port, app.s_ca = tk.StringVar(), tk.StringVar(), tk.StringVar()
    app.s_cert_pin, app.s_key, app.s_secret = tk.StringVar(), tk.StringVar(), tk.StringVar()
    app.s_format, app.s_camera = tk.StringVar(), tk.StringVar()
    app.s_preferred_group = tk.StringVar()
    app.s_valid_times = tk.StringVar(value="0")
    app.s_face_enabled = tk.BooleanVar(value=True)
    app.s_face_stable = tk.StringVar(value="900")
    app.s_face_sensitivity = tk.StringVar(value="Normal")
    app.s_face_quality = tk.BooleanVar(value=True)
    app.s_face_timeout = tk.StringVar(value="35")

    # Gateway.
    gateway = _card(scroller)
    _section(gateway, 1, "Integração HikCentral", "OpenAPI local com canal seguro", "▤")
    grid = ctk.CTkFrame(gateway, fg_color="transparent")
    grid.pack(fill="x")
    grid.grid_columnconfigure((0, 1), weight=1, uniform="gateway")
    app.gateway_grid = grid
    app.gateway_cells = [
        _setting_field(grid, 0, 0, "IP/DNS", app.s_host),
        _setting_field(grid, 0, 1, "Porta OpenAPI", app.s_port),
        _setting_field(grid, 1, 0, "AppKey", app.s_key, secret=True),
        _setting_field(grid, 1, 1, "AppSecret", app.s_secret, secret=True),
        _setting_field(grid, 2, 0, "CA (PEM opcional)", app.s_ca),
        _setting_field(grid, 2, 1, "SHA-256 do certificado", app.s_cert_pin)
    ]
    pin = app.gateway_cells[-1]
    _secondary(pin, "Ler e conferir certificado", app.pin_certificate, width=192).pack(
        anchor="w", pady=(8, 0))
    _text(gateway, "Verificação TLS obrigatória: CA confiável ou certificado SHA-256 aprovado.",
          size=11, color=MUTED).pack(anchor="w", pady=(8, 0))

    # Reader and local preferred group.
    local = _card(scroller)
    _section(local, 2, "Ponto de venda", "Preferências locais deste computador", "⚙")
    localrow = ctk.CTkFrame(local, fg_color="transparent")
    localrow.pack(fill="x")
    localrow.grid_columnconfigure((0, 1), weight=1, uniform="local")
    app.local_grid = localrow
    format_area = ctk.CTkFrame(localrow, fg_color="transparent")
    format_area.grid(row=0, column=0, sticky="ew", padx=(0, 9), pady=(0, 10))
    uid_combo = _combo(format_area, "Formato do UID", app.s_format)
    uid_combo["values"] = [
        "DECIMAL_LITTLE", "DECIMAL_BIG", "DECIMAL_BITS_REVERSED",
        "DECIMAL_EACH_BYTE_BITS_REVERSED", "HEX"]
    camera_area = ctk.CTkFrame(localrow, fg_color="transparent")
    camera_area.grid(row=0, column=1, sticky="ew", padx=(9, 0), pady=(0, 10))
    _text(camera_area, "Índice da webcam", size=12, bold=True).pack(anchor="w", pady=(0, 6))
    ttk.Spinbox(camera_area, from_=0, to=9, textvariable=app.s_camera,
                font=("Segoe UI", 12), width=8).pack(anchor="w", ipady=7)
    app.local_cells = [format_area, camera_area]
    preferred_area = ctk.CTkFrame(local, fg_color="transparent")
    preferred_area.pack(fill="x")
    app.preferred_box = _combo(preferred_area, "Grupo preferencial", app.s_preferred_group,
                               state="disabled")
    app.preferred_box.configure(values=(), state="disabled")
    _text(local, "Grupo selecionado bloqueia o Cadastro neste caixa. Sem preferência permite escolher livremente.",
          size=11, color=MUTED).pack(anchor="w", pady=(9, 0))
    times_area = ctk.CTkFrame(local, fg_color="transparent")
    times_area.pack(fill="x", pady=(16, 0))
    _text(times_area, "VALID TIMES", size=12, bold=True).pack(anchor="w", pady=(0, 6))
    ttk.Spinbox(times_area, from_=0, to=100, textvariable=app.s_valid_times,
                font=("Segoe UI", 12), width=9).pack(anchor="w", ipady=7)
    _text(local,
          "0 = sem limite de utilizações; 1 a 100 = máximo de utilizações. "
          "Válido para novos visitantes. A data e a agenda do nível continuam obrigatórias.",
          size=11, color=MUTED).pack(anchor="w", pady=(7, 0))

    # Isolated offline face capture preferences; original Visitor and NFC settings unchanged.
    face_settings = _card(scroller)
    _section(face_settings, "✦", "Captura facial inteligente",
             "Enquadramento automático na webcam — processamento local", "◉")
    ctk.CTkCheckBox(face_settings, text="Fotografar automaticamente quando o rosto estiver adequado",
                    variable=app.s_face_enabled, text_color=INK).pack(anchor="w", pady=(1, 10))
    face_row = ctk.CTkFrame(face_settings, fg_color="transparent")
    face_row.pack(fill="x")
    left = ctk.CTkFrame(face_row, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True, padx=(0, 12))
    _text(left, "Tempo de estabilidade (ms)", size=12, bold=True).pack(anchor="w")
    ttk.Spinbox(left, from_=400, to=3000, increment=100, textvariable=app.s_face_stable,
                width=10, font=("Segoe UI", 12)).pack(anchor="w", ipady=6, pady=(5, 8))
    middle = ctk.CTkFrame(face_row, fg_color="transparent")
    middle.pack(side="left", fill="x", expand=True, padx=10)
    _text(middle, "Sensibilidade do enquadramento", size=12, bold=True).pack(anchor="w")
    ctk.CTkOptionMenu(middle, variable=app.s_face_sensitivity,
                      values=["Suave", "Normal", "Rigorosa"],
                      width=145).pack(anchor="w", pady=(5, 8))
    right = ctk.CTkFrame(face_row, fg_color="transparent")
    right.pack(side="left", fill="x", expand=True, padx=(10, 0))
    _text(right, "Ajuda manual após (s)", size=12, bold=True).pack(anchor="w")
    ttk.Spinbox(right, from_=10, to=120, increment=5, textvariable=app.s_face_timeout,
                width=9, font=("Segoe UI", 12)).pack(anchor="w", ipady=6, pady=(5, 8))
    ctk.CTkCheckBox(face_settings, text="Exigir luz e nitidez adequadas",
                    variable=app.s_face_quality, text_color=INK).pack(anchor="w", pady=(1, 8))
    _text(face_settings,
          "A webcam existente continua selecionada acima. O botão Capturar manualmente "
          "fica disponível mesmo sem detector ou se a foto não passar na análise.",
          size=11, color=MUTED).pack(anchor="w", pady=(3, 0))

    # Ticket catalog and access levels. Editable text preserves all original semantics.
    catalog = _card(scroller)
    _section(catalog, 3, "Ingressos e acessos", "Regras configuráveis por grupo", "▦")
    editors = ctk.CTkFrame(catalog, fg_color="transparent")
    editors.pack(fill="x")
    editors.grid_columnconfigure((0, 1), weight=1, uniform="editors")
    app.editors_grid = editors
    ticket_edit = ctk.CTkFrame(editors, fg_color="transparent")
    ticket_edit.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    _text(ticket_edit, "Ingressos · Nome;Dias;Entrada;Saída", size=12, bold=True).pack(anchor="w", pady=(0, 6))
    app.tickets_text = tk.Text(
        ticket_edit, height=7, bg="#FAFCFF", fg=INK, insertbackground=INK,
        relief="flat", highlightthickness=1, highlightbackground="#D5DFEB",
        font=("Consolas", 11), wrap="none", padx=11, pady=9)
    app.tickets_text.pack(fill="both", expand=True)
    levels_edit = ctk.CTkFrame(editors, fg_color="transparent")
    levels_edit.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    _text(levels_edit, "Níveis de acesso por grupo Visitor", size=12, bold=True).pack(
        anchor="w", pady=(0, 6))
    app.access_text = tk.Text(
        levels_edit, height=7, bg="#FAFCFF", fg=INK, insertbackground=INK,
        relief="flat", highlightthickness=1, highlightbackground="#D5DFEB",
        font=("Consolas", 10), wrap="none", padx=11, pady=9)
    app.access_text.pack(fill="both", expand=True)
    app.editor_cells = [ticket_edit, levels_edit]
    _text(
        catalog,
        "Padrão atual: Day use=DAY_USE (8 pontos), Hóspedes Serra=VISITANTE_SERRA (8 pontos), "
        "Hóspedes Vale=VISITANTE_VALE (3 pontos). Todos usam a agenda ACQUAVALE. "
        "Cada grupo exige um nível Visitor type=2; nunca níveis All-Day de funcionários.",
        size=11, color=MUTED).pack(anchor="w", pady=(8, 0))

    _text(catalog, "Agendas diárias verificadas no HikCentral · Nível=09:00-17:00",
          size=12, bold=True).pack(anchor="w", pady=(15, 6))
    app.schedule_text = tk.Text(
        catalog, height=5, bg="#FAFCFF", fg=INK, insertbackground=INK,
        relief="flat", highlightthickness=1, highlightbackground="#D5DFEB",
        font=("Consolas", 10), wrap="none", padx=11, pady=9)
    app.schedule_text.pack(fill="x")
    _text(
        catalog,
        "DAY_USE e VISITANTE_SERRA: 8 pontos cada. VISITANTE_VALE: 3 pontos. "
        "Agenda ACQUAVALE 09:00–17:00 diariamente conforme informado pelo hotel. "
        "O aplicativo apenas consulta níveis e distribui credenciais nos pontos retornados; "
        "não altera agendas, níveis ou catracas.",
        size=11, color="#A05A14").pack(anchor="w", pady=(8, 0))


def build(app):
    ctk.set_appearance_mode("light")
    width, height, x, y = responsive_ui.initial_geometry(
        app.winfo_screenwidth(), app.winfo_screenheight())
    app.geometry(f"{width}x{height}+{x}+{y}")
    app.minsize(min(870, width), min(540, height))
    app.configure(bg=BG)
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Vale.Page.TFrame", background=BG, borderwidth=0)
    style.configure("Vale.TCombobox", fieldbackground=WHITE, background=WHITE,
                    bordercolor="#D2DDEA", darkcolor="#D2DDEA", lightcolor="#D2DDEA",
                    arrowcolor=INK, padding=8)
    style.map("Vale.TCombobox", fieldbackground=[("readonly", WHITE)],
              selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
    # Notebook remains for existing authentication and regression checks;
    # actual navigation is the modern sidebar.
    style.layout("Vale.Hidden.TNotebook.Tab", [])
    style.configure("Vale.Hidden.TNotebook", background=BG, borderwidth=0, padding=0)
    app.status = tk.StringVar(value="Verificando configurações do HikCentral...")
    app.connection_label_var = tk.StringVar(value="Aguardando conexão")
    app.main_shell = ctk.CTkFrame(app, fg_color=BG, corner_radius=0)
    app.main_shell.pack(fill="both", expand=True)

    sidebar = ctk.CTkFrame(
        app.main_shell, width=220, corner_radius=0, fg_color=NAVY)
    app.sidebar = sidebar
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)
    logo = ctk.CTkFrame(sidebar, fg_color="transparent")
    logo.pack(fill="x", padx=25, pady=(27, 31))
    ctk.CTkLabel(logo, text="◆", text_color=CYAN,
                 font=("Segoe UI", 32, "bold"), width=38).pack(side="left")
    _text(logo, "VALE", size=26, color=WHITE, bold=True).pack(side="left", padx=5)
    _text(sidebar, "Cadastro Facial\ne NFC", size=15,
          color="#71E6F1", bold=True).pack(anchor="w", padx=29, pady=(0, 23))

    main = ctk.CTkFrame(app.main_shell, fg_color=BG, corner_radius=0)
    main.pack(side="left", fill="both", expand=True)

    top = ctk.CTkFrame(main, height=106, fg_color=WHITE,
                       corner_radius=0, border_width=1, border_color=LINE)
    top.pack(side="top", fill="x")
    top.pack_propagate(False)
    heading = ctk.CTkFrame(top, fg_color="transparent")
    heading.pack(side="left", padx=30, pady=20)
    _text(heading, "Cadastro de Visitante", size=24, bold=True).pack(anchor="w")
    app.header_subtitle = _text(heading, "Registro facial e NFC com integração local ao HikCentral",
                                size=12, color=MUTED)
    app.header_subtitle.pack(anchor="w")
    connection = ctk.CTkFrame(top, fg_color="transparent")
    app.connection_header = connection
    connection.pack(side="right", padx=24, pady=25)
    _text(connection, "●  HikCentral (local)", size=13, bold=True).pack(anchor="w")
    ctk.CTkLabel(connection, textvariable=app.connection_label_var,
                 text_color="#159965", font=("Segoe UI", 12)).pack(anchor="w")

    app.book = ttk.Notebook(main, style="Vale.Hidden.TNotebook")
    app.book.pack(fill="both", expand=True)
    app.registration_tab = ttk.Frame(app.book, style="Vale.Page.TFrame")
    app.settings_tab = ttk.Frame(app.book, style="Vale.Page.TFrame")
    app.history_tab = ttk.Frame(app.book, style="Vale.Page.TFrame")
    app.book.add(app.registration_tab, text="Cadastro")
    app.book.add(app.settings_tab, text="Configurações")
    app.book.add(app.history_tab, text="Cadastros efetuados")
    app.nav_registration = _nav_button(app, sidebar, "Cadastro", "◉", app.registration_tab)
    app.nav_history = _nav_button(app, sidebar, "Cadastros efetuados", "▤", app.history_tab)
    app.nav_settings = _nav_button(app, sidebar, "Configurações", "⚙", app.settings_tab)
    ctk.CTkFrame(sidebar, fg_color="transparent").pack(fill="both", expand=True)
    status_box = ctk.CTkFrame(
        sidebar, fg_color=NAVY_2, corner_radius=12)
    status_box.pack(fill="x", padx=17, pady=(0, 14))
    _text(status_box, "Integração local\ncom HikCentral", color="#BBCEE4", size=11).pack(
        anchor="w", padx=14, pady=(12, 3))
    ctk.CTkLabel(status_box, textvariable=app.connection_label_var,
                 text_color="#5DE5AD", font=("Segoe UI", 12, "bold")).pack(
        anchor="w", padx=14, pady=(0, 12))
    _text(sidebar, f"v{app._build_version}", color="#9BB1C9", size=10).pack(
        anchor="w", padx=24, pady=(0, 19))

    _registration(app)
    _settings(app)
    _history(app)
    responsive_ui.apply(app, width)
    app.bind("<Configure>", lambda event: responsive_ui.on_resize(app, event), add="+")
    app.book.bind("<<NotebookTabChanged>>", app._tab_changed)
    app.status.set("Sincronização automática ao iniciar..." if app.key and app.secret
                   else "Configure AppKey/AppSecret em Configurações.")
    app.first_entry.focus_set()


def update_navigation(app):
    if not hasattr(app, "nav_registration"):
        return
    selected = app.book.select()
    for button, active in (
        (app.nav_registration, selected == str(app.registration_tab)),
        (app.nav_history, selected == str(app.history_tab)),
        (app.nav_settings, selected == str(app.settings_tab)),
    ):
        button.configure(
            fg_color=BLUE if active else "transparent",
            text_color=WHITE if active else "#DBE7F6")
