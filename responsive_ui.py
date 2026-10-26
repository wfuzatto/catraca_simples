"""Adaptive desktop presentation for the Vale Windows reception app."""
from __future__ import annotations


def initial_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """Stay inside the visible Windows desktop; leave room for taskbar."""
    w = max(550, int(screen_width))
    h = max(460, int(screen_height))
    window_w = min(1420, max(540, w - 30))
    window_h = min(920, max(440, h - 90))
    return window_w, window_h, max(0, (w - window_w) // 2), max(0, (h - window_h - 40) // 2)


def _columns(frame, cells, count: int) -> None:
    for index, widget in enumerate(cells):
        widget.grid_configure(
            row=index // count, column=index % count,
            sticky="nsew", padx=6, pady=(0, 9)
        )
    for col in range(3):
        frame.grid_columnconfigure(col, weight=1 if col < count else 0,
                                   uniform="equal" if count > 1 else "")


def apply(app, width: int) -> None:
    compact = width < 1150
    narrow = width < 940
    mode = "narrow" if narrow else ("compact" if compact else "wide")
    if getattr(app, "_responsive_mode", None) == mode:
        return
    app._responsive_mode = mode

    _columns(app.visitor_grid, app.visitor_cells, 1 if narrow else 2)
    _columns(app.credential_grid, app.credential_cells, 1 if compact else 2)
    _columns(app.ticket_grid, app.ticket_cells, 1 if compact else 3)
    _columns(app.gateway_grid, app.gateway_cells, 1 if narrow else 2)
    _columns(app.local_grid, app.local_cells, 1 if narrow else 2)
    _columns(app.editors_grid, app.editor_cells, 1 if compact else 2)

    if narrow:
        app.sidebar.configure(width=165)
        for btn in (app.nav_registration, app.nav_settings):
            btn.configure(width=132)
            btn.pack_configure(padx=6)
        app.connection_header.pack_forget()
        app.header_subtitle.pack_forget()
    else:
        app.sidebar.configure(width=220)
        for btn in (app.nav_registration, app.nav_settings):
            btn.configure(width=182)
            btn.pack_configure(padx=13)
        if not app.connection_header.winfo_manager():
            app.connection_header.pack(side="right", padx=24, pady=25)
        if not app.header_subtitle.winfo_manager():
            app.header_subtitle.pack(anchor="w")

    app.registration_status_widget.pack_forget()
    app.register_button.pack_forget()
    app.delivery_button.pack_forget()
    app.settings_notice.pack_forget()
    app.save_button.pack_forget()
    app.settings_test_button.pack_forget()
    app.settings_diagnostic_button.pack_forget()
    if compact:
        app.registration_footer.configure(height=101)
        app.registration_status_widget.pack(side="top", fill="x", padx=14, pady=(7, 0))
        app.register_button.pack(side="right", padx=(6, 14), pady=(2, 7))
        app.delivery_button.pack(side="right", padx=(6, 0), pady=(2, 7))
        app.settings_footer.configure(height=102)
        app.settings_notice.pack(side="top", fill="x", padx=14, pady=(7, 0))
        app.save_button.pack(side="right", padx=(6, 14), pady=(2, 7))
        app.settings_test_button.pack(side="right", padx=(6, 0), pady=(2, 7))
        app.settings_diagnostic_button.pack(side="right", padx=(6, 0), pady=(2, 7))
    else:
        app.registration_footer.configure(height=73)
        app.register_button.pack(side="right", padx=(8, 18), pady=13)
        app.delivery_button.pack(side="right", padx=(8, 0), pady=13)
        app.registration_status_widget.pack(side="left", fill="x", expand=True, padx=18)
        app.settings_footer.configure(height=75)
        app.save_button.pack(side="right", padx=(9, 18), pady=13)
        app.settings_test_button.pack(side="right", padx=(9, 0), pady=13)
        app.settings_diagnostic_button.pack(side="right", padx=(9, 0), pady=13)
        app.settings_notice.pack(side="left", padx=18)


def on_resize(app, event) -> None:
    if event.widget is app:
        apply(app, event.width)
