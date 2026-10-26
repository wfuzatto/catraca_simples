"""Modern visual shell for Vale visitor registration (presentation only).

All operations remain implemented by app.ValeApp: OpenAPI, NFC, webcam,
registration, authorization, TLS, credential storage and recovery.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

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
    ctk.CTkLabel(footer, textvariable=app.status, anchor="w",
                 text_color=MUTED, font=("Segoe UI", 11), width=325,
                 wraplength=390).pack(side="left", fill="x", expand=True, padx=21)

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

    # 2. Credentials.
    credentials = _card(left)
    _section(credentials, 2, "Credenciais de acesso",
             "Reconhecimento facial e/ou pulseira NFC", "◆")
    creds_row = ctk.CTkFrame(credentials, fg_color="transparent")
    creds_row.pack(fill="x")
    creds_row.grid_columnconfigure((0, 1), weight=1, uniform="credentials")
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
        width=17, height=7, font=("Segoe UI", 10))
    app.photo_widget.pack(expand=True, pady=5)
    choices = ctk.CTkFrame(photo_inner, fg_color="transparent")
    choices.pack(side="left", fill="both", expand=True)
    _primary(choices, "Escolher foto facial", app.browse_photo, width=174).pack(
        fill="x", pady=(7, 8))
    _secondary(choices, "Foto pela webcam", app.camera, width=174).pack(fill="x")

    # 3. Ticket.
    period = _card(left)
    _section(period, 3, "Ingresso e validade",
             "Defina o tipo de ingresso e o período de acesso", "▦")
    ticket_row = ctk.CTkFrame(period, fg_color="transparent")
    ticket_row.pack(fill="x")
    ticket_row.grid_columnconfigure((0, 1, 2), weight=1, uniform="validity")
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
    app.start.trace_add("write", lambda *_: app.calculate())
    _text(period, "Saída calculada pelo ingresso; você pode ajustar o horário.",
          color=MUTED, size=11).pack(anchor="w", pady=(10, 0))


def _setting_field(parent, row, column, title, variable, *, secret=False):
    cell = ctk.CTkFrame(parent, fg_color="transparent")
    cell.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 12,
                                                         12 if column == 0 else 0), pady=8)
    _field(cell, title, variable, show="*" if secret else None)
    return cell


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
    _secondary(actions, "Testar conexão", app.connect, width=150).pack(
        side="right", padx=(9, 0), pady=13)
    _secondary(actions, "Diagnóstico de rede", app.diagnose, width=164).pack(
        side="right", padx=(9, 0), pady=13)
    _text(actions, "Alterações só são gravadas ao clicar em Salvar.",
          size=11, color=MUTED).pack(side="left", padx=18)

    scroller = ctk.CTkScrollableFrame(
        wrapper, fg_color=BG, corner_radius=0,
        scrollbar_button_color="#CFDAEB")
    scroller.pack(fill="both", expand=True, padx=18, pady=13)

    app.s_host, app.s_port, app.s_ca = tk.StringVar(), tk.StringVar(), tk.StringVar()
    app.s_cert_pin, app.s_key, app.s_secret = tk.StringVar(), tk.StringVar(), tk.StringVar()
    app.s_format, app.s_camera = tk.StringVar(), tk.StringVar()
    app.s_preferred_group = tk.StringVar()

    # Gateway.
    gateway = _card(scroller)
    _section(gateway, 1, "Integração HikCentral", "OpenAPI local com canal seguro", "▤")
    grid = ctk.CTkFrame(gateway, fg_color="transparent")
    grid.pack(fill="x")
    grid.grid_columnconfigure((0, 1), weight=1, uniform="gateway")
    _setting_field(grid, 0, 0, "IP/DNS", app.s_host)
    _setting_field(grid, 0, 1, "Porta OpenAPI", app.s_port)
    _setting_field(grid, 1, 0, "AppKey", app.s_key, secret=True)
    _setting_field(grid, 1, 1, "AppSecret", app.s_secret, secret=True)
    _setting_field(grid, 2, 0, "CA (PEM opcional)", app.s_ca)
    pin = _setting_field(grid, 2, 1, "SHA-256 do certificado", app.s_cert_pin)
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
    preferred_area = ctk.CTkFrame(local, fg_color="transparent")
    preferred_area.pack(fill="x")
    app.preferred_box = _combo(preferred_area, "Grupo preferencial", app.s_preferred_group,
                               state="disabled")
    app.preferred_box.configure(values=(), state="disabled")
    _text(local, "Escolhido após sincronizar os grupos reais; o operador poderá alterar no cadastro.",
          size=11, color=MUTED).pack(anchor="w", pady=(9, 0))

    # Ticket catalog and access levels. Editable text preserves all original semantics.
    catalog = _card(scroller)
    _section(catalog, 3, "Ingressos e acessos", "Regras configuráveis por grupo", "▦")
    editors = ctk.CTkFrame(catalog, fg_color="transparent")
    editors.pack(fill="x")
    editors.grid_columnconfigure((0, 1), weight=1, uniform="editors")
    ticket_edit = ctk.CTkFrame(editors, fg_color="transparent")
    ticket_edit.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    _text(ticket_edit, "Ingressos · Nome;Dias;HH:MM", size=12, bold=True).pack(anchor="w", pady=(0, 6))
    app.tickets_text = tk.Text(
        ticket_edit, height=7, bg="#FAFCFF", fg=INK, insertbackground=INK,
        relief="flat", highlightthickness=1, highlightbackground="#D5DFEB",
        font=("Consolas", 11), wrap="none", padx=11, pady=9)
    app.tickets_text.pack(fill="both", expand=True)
    levels_edit = ctk.CTkFrame(editors, fg_color="transparent")
    levels_edit.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    _text(levels_edit, "Níveis de acesso por grupo", size=12, bold=True).pack(anchor="w", pady=(0, 6))
    app.access_text = tk.Text(
        levels_edit, height=7, bg="#FAFCFF", fg=INK, insertbackground=INK,
        relief="flat", highlightthickness=1, highlightbackground="#D5DFEB",
        font=("Consolas", 10), wrap="word", padx=11, pady=9)
    app.access_text.pack(fill="both", expand=True)
    _text(catalog, "Use apenas os níveis Visitor autorizados no HikCentral. Grupos sem níveis ficam bloqueados.",
          size=11, color=MUTED).pack(anchor="w", pady=(8, 0))


def build(app):
    ctk.set_appearance_mode("light")
    app.geometry("1420x920")
    app.minsize(1020, 700)
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
    _text(heading, "Registro facial e NFC com integração local ao HikCentral",
          size=12, color=MUTED).pack(anchor="w")
    connection = ctk.CTkFrame(top, fg_color="transparent")
    connection.pack(side="right", padx=24, pady=25)
    _text(connection, "●  HikCentral (local)", size=13, bold=True).pack(anchor="w")
    ctk.CTkLabel(connection, textvariable=app.connection_label_var,
                 text_color="#159965", font=("Segoe UI", 12)).pack(anchor="w")

    app.book = ttk.Notebook(main, style="Vale.Hidden.TNotebook")
    app.book.pack(fill="both", expand=True)
    app.registration_tab = ttk.Frame(app.book, style="Vale.Page.TFrame")
    app.settings_tab = ttk.Frame(app.book, style="Vale.Page.TFrame")
    app.book.add(app.registration_tab, text="Cadastro")
    app.book.add(app.settings_tab, text="Configurações")
    app.nav_registration = _nav_button(app, sidebar, "Cadastro", "◉", app.registration_tab)
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
        (app.nav_settings, selected == str(app.settings_tab)),
    ):
        button.configure(
            fg_color=BLUE if active else "transparent",
            text_color=WHITE if active else "#DBE7F6")
