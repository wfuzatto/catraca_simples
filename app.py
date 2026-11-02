"""Vale Cadastro Facial / NFC - aplicacao Windows local."""
from __future__ import annotations
import io
import json
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageTk

from core import (APP_VERSION, GROUPS, TZ, Visitor, load_settings, save_settings, load_api_keys,
                  initial_visitor_group, validate_visitor_group_selection, validate_valid_times,
                  visitor_levels_for_group, verify_visitor_access_plan,
                  mark_appointment_inflight,
                  save_api_keys, parse_local, end_for_ticket, registration_payload,
                  prepare_face_jpeg, face_cover_preview, normalize_ticket_start,
                  validate_ticket_period, require_confirmed_daily_schedule,
                  parse_ticket_lines, parse_access_lines, parse_schedule_lines, load_pending, save_pending,
                  mark_assigned, mark_registered, clear_pending,
                  save_last_delivery, load_last_delivery)
from devices import list_readers, read_uid, to_card_number
from hikcentral import HikCentral, network_diagnostics, read_cert_sha256, normalize_pin
from settings_auth import verify_settings_password
import modern_ui
from visitor_history import list_history, save_registration, set_history_status, get_history

PREFERRED_AUTOMATIC = "Sem preferência (primeiro disponível)"
PREFERRED_PENDING = "Aguardando grupos do HikCentral"




def selectable_visitor_groups(groups: list[str]) -> list[str]:
    """Mostra grupos reais da API, exceto o grupo genérico Visitors."""
    selected = []
    seen = set()
    for value in groups:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if not name or name.casefold() == "visitors":
            continue
        if name.casefold() not in seen:
            selected.append(name)
            seen.add(name.casefold())
    return selected


class ValeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Vale - Cadastro Facial / NFC v{APP_VERSION}")
        self.geometry("920x730")
        self.minsize(820, 660)
        self.settings = load_settings()
        try:
            self.key, self.secret = load_api_keys()
        except Exception as exc:
            self.key, self.secret = "", ""
            messagebox.showwarning("Segredos", str(exc))
        self.connected_groups = []
        self.verified = False
        self._preferred_groups_synced = False
        self._force_preferred_on_sync = False
        self.photo_data = b""
        self.preview = None
        self.busy = False
        self._recovery_attempts = 0
        self._settings_unlocked = False
        self._settings_auth_prompt_queued = False
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TButton", padding=7)
        style.configure("Register.TButton", font=("Segoe UI", 10, "bold"), padding=(18, 9))
        style.configure("TNotebook.Tab", padding=(16, 8))
        self._build()
        self._fill_settings()
        # A sincronização e a retomada segura acontecem sem botões manuais.
        self.after(120, self._startup_sync)

    def _entry(self, frame, row, col, label, var, show=None):
        ttk.Label(frame, text=label).grid(row=row, column=col * 2, sticky="w", padx=4, pady=6)
        field = ttk.Entry(frame, textvariable=var, width=30, show=show or "")
        field.grid(row=row, column=col * 2 + 1, sticky="ew", padx=4, pady=6)
        return field

    def _build(self):
        """Modern visual shell; registration and OpenAPI logic stay unchanged."""
        self._build_version = APP_VERSION
        modern_ui.build(self)

    def _registration(self):
        frame = ttk.LabelFrame(self.registration_tab, text="Visitante", padding=10)
        frame.pack(fill="x", pady=9)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        self.first, self.last, self.group = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.reservation = tk.StringVar()
        self._entry(frame, 0, 0, "Nome", self.first)
        self._entry(frame, 0, 1, "Sobrenome", self.last)
        ttk.Label(frame, text="Grupo Visitor").grid(row=1, column=0, sticky="w", padx=4)
        self.groups_box = ttk.Combobox(frame, textvariable=self.group, state="readonly", values=())
        self.groups_box.grid(row=1, column=1, sticky="ew", padx=4)
        self._entry(frame, 1, 1, "Reserva ou Apartamento", self.reservation)

        card = ttk.LabelFrame(self.registration_tab, text="Credenciais: reconhecimento facial e/ou pulseira NFC", padding=10)
        card.pack(fill="x", pady=7)
        card_left = ttk.Frame(card)
        card_left.pack(side="left", fill="both", expand=True, padx=(0, 18))
        self.card = tk.StringVar()
        self.uid = tk.StringVar(value="UID não lido")
        ttk.Label(card_left, text="N\u00famero do cart\u00e3o / pulseira para HikCentral (opcional)").pack(anchor="w")
        ttk.Entry(card_left, textvariable=self.card, width=35).pack(fill="x", pady=7)
        ttk.Button(card_left, text="Ler ACR122U (PC/SC)", command=self.scan).pack(anchor="w")
        # Resultados técnicos do UID não ocupam a tela de cadastro.
        # O cartão convertido continua visível no campo acima.
        self.photo_widget = tk.Label(card, text="SEM FOTO", width=20, height=7, bg="#e5edf3")
        self.photo_widget.pack(side="left", padx=10)
        controls = ttk.Frame(card)
        controls.pack(side="left")
        ttk.Button(controls, text="Escolher foto facial (opcional)", command=self.browse_photo).pack(fill="x", pady=5)
        ttk.Button(controls, text="Foto facial pela webcam", command=self.camera).pack(fill="x", pady=5)

        period = ttk.LabelFrame(self.registration_tab, text="Ingresso e validade", padding=10)
        period.pack(fill="x", pady=7)
        period.columnconfigure(1, weight=1)
        period.columnconfigure(3, weight=1)
        self.ticket, self.start, self.end = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(period, text="Ingresso").grid(row=0, column=0, sticky="w", padx=4)
        self.ticket_box = ttk.Combobox(period, textvariable=self.ticket, state="readonly")
        self.ticket_box.grid(row=0, column=1, sticky="ew", padx=4)
        self.ticket_box.bind("<<ComboboxSelected>>", lambda _: self.calculate())
        self.start.trace_add("write", lambda *_: self.calculate())
        self._entry(period, 1, 0, "Entrada", self.start)
        self._entry(period, 1, 1, "Saída", self.end)
        # Campos permanecem editáveis; o horário final é recalculado ao editar a entrada.

    def _settings(self):
        self.s_host, self.s_port, self.s_ca = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.s_cert_pin = tk.StringVar()
        self.s_key, self.s_secret = tk.StringVar(), tk.StringVar()
        self.s_format, self.s_camera = tk.StringVar(), tk.StringVar()
        frame = ttk.LabelFrame(self.settings_tab, text="OpenAPI local", padding=10)
        frame.pack(fill="x", pady=8)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        self._entry(frame, 0, 0, "IP/DNS", self.s_host)
        self._entry(frame, 0, 1, "Porta OpenAPI", self.s_port)
        self._entry(frame, 1, 0, "CA (PEM opcional)", self.s_ca)
        self._entry(frame, 2, 0, "AppKey", self.s_key, show="*")
        self._entry(frame, 2, 1, "AppSecret", self.s_secret, show="*")
        self._entry(frame, 3, 0, "SHA-256 cert. local", self.s_cert_pin)
        ttk.Button(frame, text="Ler e conferir certificado", command=self.pin_certificate).grid(
            row=3, column=2, columnspan=2, sticky="w", padx=5)
        ttk.Label(frame, text="TLS seguro: CA + DNS correto, OU certificado SHA-256 aprovado manualmente.").grid(
            row=4, column=0, columnspan=4, sticky="w", pady=6)
        device = ttk.LabelFrame(self.settings_tab, text="Dispositivos", padding=10)
        device.pack(fill="x", pady=6)
        ttk.Label(device, text="Formato do UID").pack(side="left")
        ttk.Combobox(device, textvariable=self.s_format, state="readonly",
                     values=["DECIMAL_LITTLE", "DECIMAL_BIG", "DECIMAL_BITS_REVERSED",
                             "DECIMAL_EACH_BYTE_BITS_REVERSED", "HEX"], width=30).pack(side="left", padx=8)
        ttk.Label(device, text="Índice da webcam").pack(side="left")
        ttk.Spinbox(device, from_=0, to=9, textvariable=self.s_camera, width=4).pack(side="left", padx=6)

        local = ttk.LabelFrame(self.settings_tab, text="Padrão deste ponto de venda", padding=(10, 7))
        local.pack(fill="x", pady=(4, 3))
        ttk.Label(local, text="Grupo preferencial").pack(side="left", padx=(0, 8))
        self.s_preferred_group = tk.StringVar(value=PREFERRED_PENDING)
        self.preferred_box = ttk.Combobox(
            local, textvariable=self.s_preferred_group, state="disabled",
            values=(), width=29)
        self.preferred_box.pack(side="left", padx=(0, 14))
        ttk.Label(
            local, text="Carregado do HikCentral. Apenas sugere o grupo inicial; operador pode mudar."
        ).pack(side="left")

        options = ttk.Frame(self.settings_tab)
        options.pack(fill="both", expand=True, pady=9)
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)
        options.rowconfigure(0, weight=1)
        tickets = ttk.LabelFrame(options, text="Ingressos: Nome;Dias;HH:MM", padding=8)
        tickets.grid(row=0, column=0, sticky="nsew", padx=6)
        self.tickets_text = tk.Text(tickets, height=8, width=26, font=("Consolas", 10))
        self.tickets_text.pack(fill="both", expand=True)
        access = ttk.LabelFrame(options, text="Níveis de acesso por grupo", padding=8)
        access.grid(row=0, column=1, sticky="nsew", padx=6)
        self.access_text = tk.Text(access, height=8, width=44, font=("Consolas", 9), wrap="word")
        self.access_text.pack(fill="both", expand=True)
        ttk.Label(self.settings_tab,
                  text="Use os nomes exatos dos níveis Visitor sincronizados com o HikCentral (ex.: ENTRADA ACQUAVALE). "
                       "'type=2' é um filtro técnico, não um tipo para cadastrar. Grupos sem níveis ficam bloqueados para cadastro.",
                  wraplength=850).pack(anchor="w", padx=8)
        ttk.Button(self.settings_tab, text="Salvar", command=self.save_config).pack(side="right", pady=5)
        ttk.Button(self.settings_tab, text="Testar conexão", command=self.connect).pack(side="right", padx=9)
        ttk.Button(self.settings_tab, text="Diagnóstico de rede",
                   command=self.diagnose).pack(side="right", padx=9)

    def _fill_settings(self):
        s = self.settings
        for var, key in [(self.s_host, "host"), (self.s_port, "port"), (self.s_ca, "ca_file"),
                         (self.s_format, "card_format"), (self.s_camera, "camera_index")]:
            var.set(str(s[key]))
        self.s_key.set(self.key)
        self.s_secret.set(self.secret)
        self.s_cert_pin.set(s.get("cert_pin_sha256", ""))
        self.s_valid_times.set(str(s.get("valid_times", 0)))
        self.s_face_enabled.set(bool(s.get("face_auto_capture", True)))
        self.s_face_stable.set(str(s.get("face_stability_ms", 900)))
        self.s_face_sensitivity.set(s.get("face_sensitivity", "Normal"))
        self.s_face_quality.set(bool(s.get("face_require_quality", True)))
        self.s_face_timeout.set(str(s.get("face_timeout_s", 35)))
        # Não preencher o select com grupos presumidos ou valores antigos.
        self.s_preferred_group.set(PREFERRED_PENDING)
        self.preferred_box.configure(values=(), state="disabled")
        self._preferred_groups_synced = False
        self.tickets_text.delete("1.0", tk.END)
        self.tickets_text.insert("1.0", "\n".join(
            f"{name};{opt['days']};{opt.get('checkin', '09:00')};{opt['checkout']}"
            for name, opt in s["tickets"].items()))
        self.schedule_text.delete("1.0", tk.END)
        self.schedule_text.insert("1.0", "\n".join(
            f"{name}={window}" for name, window in
            s.get("schedule_windows", {}).items()))
        self.access_text.delete("1.0", tk.END)
        self.access_text.insert("1.0", "\n".join(
            f"{name}={','.join(levels)}" for name, levels in s["group_access"].items()))
        self.ticket_box["values"] = list(s["tickets"])
        self.ticket.set(next(iter(s["tickets"])))
        self.start.set(datetime.now(TZ).strftime("%d/%m/%Y %H:%M"))
        self.calculate()

    def calculate(self):
        """Respeita início/fim do expediente do ingresso e calcula último dia."""
        try:
            ticket = self.settings["tickets"][self.ticket.get()]
            requested = parse_local(self.start.get())
            aligned = normalize_ticket_start(requested, ticket)
            if aligned != requested:
                formatted_start = aligned.strftime("%d/%m/%Y %H:%M")
                if self.start.get() != formatted_start:
                    self.start.set(formatted_start)
                    # Um callback trace do Tk não reentra automaticamente.
                    # O fim precisa ser recalculado nesta mesma execução.
            result = end_for_ticket(aligned, ticket)
        except (KeyError, ValueError):
            return
        formatted = result.strftime("%d/%m/%Y %H:%M")
        if self.end.get() != formatted:
            self.end.set(formatted)

    def save_config(self, silent=False):
        try:
            # Preferência só é editável após a consulta real ao HikCentral.
            # Sem sincronização, salvar outras configurações preserva a preferência antiga.
            preferred = self.settings.get("preferred_group", "")
            if self._preferred_groups_synced:
                choice = self.s_preferred_group.get()
                if choice == PREFERRED_AUTOMATIC:
                    preferred = ""
                elif choice in self.connected_groups:
                    preferred = choice
                else:
                    raise ValueError("Escolha um grupo retornado pelo HikCentral ou Sem preferência.")
            conf = {
                "host": self.s_host.get().strip(),
                "port": int(self.s_port.get()),
                "ca_file": self.s_ca.get().strip(),
                "cert_pin_sha256": normalize_pin(self.s_cert_pin.get()),
                "preferred_group": preferred,
                "card_format": self.s_format.get(),
                "camera_index": int(self.s_camera.get()),
                "face_auto_capture": bool(self.s_face_enabled.get()),
                "face_stability_ms": int(self.s_face_stable.get()),
                "face_sensitivity": self.s_face_sensitivity.get(),
                "face_require_quality": bool(self.s_face_quality.get()),
                "face_timeout_s": int(self.s_face_timeout.get()),
                "valid_times": validate_valid_times(int(self.s_valid_times.get().strip())),
                "tickets": parse_ticket_lines(self.tickets_text.get("1.0", tk.END)),
                "group_access": parse_access_lines(self.access_text.get("1.0", tk.END)),
                "access_mapping_revision": self.settings.get("access_mapping_revision", 1),
                "schedule_windows": parse_schedule_lines(
                    self.schedule_text.get("1.0", tk.END))
            }
            from face_capture_logic import FaceGuideConfig
            FaceGuideConfig.from_settings(conf)
            if not conf["host"] or not 1 <= conf["port"] <= 65535:
                raise ValueError("Host/porta inválidos.")
            if conf["ca_file"] and not Path(conf["ca_file"]).is_file():
                raise ValueError("Arquivo CA inválido.")
            if conf["ca_file"] and conf["cert_pin_sha256"]:
                raise ValueError("Não combine CA PEM e impressão SHA-256. Selecione um método.")
            key, secret = self.s_key.get().strip(), self.s_secret.get().strip()
            if not key or not secret:
                raise ValueError("Configure a AppKey e AppSecret (OpenAPI).")
            save_api_keys(key, secret)
            save_settings(conf)
            if conf["preferred_group"] != self.settings.get("preferred_group", ""):
                self._force_preferred_on_sync = True
            self.settings, self.key, self.secret = conf, key, secret
            self.verified = False
            self.ticket_box["values"] = list(conf["tickets"])
            if self.ticket.get() not in conf["tickets"]:
                self.ticket.set(next(iter(conf["tickets"])))
            self.calculate()
            if not silent:
                messagebox.showinfo("Configuração", "Configurações salvas. A sincronização será automática.")
                self.after(100, lambda: self.connect(silent=True))
            return True
        except Exception as exc:
            self.status.set("Configurações não salvas: " + str(exc))
            if not silent:
                messagebox.showerror("Configuração", str(exc))
            return False

    def client(self):
        s = self.settings
        return HikCentral(s["host"], s["port"], self.key, self.secret, s["ca_file"],
                          pinned_sha256=s.get("cert_pin_sha256", ""))

    def task(self, work, finish, *, registration_loading=False):
        if self.busy:
            messagebox.showwarning("Em andamento", "Operação anterior em andamento.")
            return
        self.busy = True
        loading_started_at = time.monotonic() if registration_loading else 0.0
        if registration_loading:
            self.status.set("Cadastrando visitante no HikCentral, aguarde...")
            modern_ui.start_registration_loading(self)
        else:
            self.status.set("Consultando HikCentral...")
        def run():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, exc
            # Tk UI work remains on the main thread.
            self.after(0, lambda: done(value, error))
        def done(value, error):
            # Keep the progress window visible even for a very fast API response.
            if registration_loading:
                elapsed_ms = int((time.monotonic() - loading_started_at) * 1000)
                if elapsed_ms < 700:
                    self.after(700 - elapsed_ms, lambda: finish_task(value, error))
                    return
            finish_task(value, error)
        def finish_task(value, error):
            self.busy = False
            if registration_loading:
                modern_ui.stop_registration_loading(self)
            finish(value, error)
        threading.Thread(target=run, daemon=True).start()

    def pin_certificate(self):
        """Operador confere SHA-256 com o certificado no próprio servidor."""
        try:
            host = self.s_host.get().strip()
            port = int(self.s_port.get())
        except ValueError:
            messagebox.showerror("Certificado", "Porta inválida.")
            return

        def finish(value, error):
            if error:
                messagebox.showerror("Certificado", str(error))
                return
            formatted = ":".join(value[i:i + 2] for i in range(0, 64, 2))
            msg = (f"Certificado apresentado por {host}:{port}:\n\n"
                   f"SHA-256:\n{formatted}\n\n"
                   "ATENÇÃO: confira esta impressão digital com a do certificado "
                   "instalado no próprio servidor HikCentral, por uma fonte confiável. "
                   "Não aprove apenas porque foi exibida aqui. "
                   "Ao aceitar, o aplicativo enviará dados somente se o servidor "
                   "apresentar exatamente este certificado.\n\n"
                   "Você conferiu o SHA-256 e deseja fixá-lo?")
            if not messagebox.askyesno("Confiar no certificado do HikCentral?", msg):
                return
            if self.s_ca.get().strip():
                messagebox.showerror("Certificado", "Remova o caminho CA PEM antes de fixar SHA-256.")
                return
            self.s_cert_pin.set(value)
            if self.save_config(silent=True):
                messagebox.showinfo("Certificado", "Impressão SHA-256 salva. Teste a OpenAPI.")

        self.task(lambda: read_cert_sha256(host, port), finish)

    def diagnose(self):
        """Testa TCP/TLS sem credenciais; mostra motivo da falha de conexão."""
        try:
            host = self.s_host.get().strip()
            port = int(self.s_port.get().strip())
            cafile = self.s_ca.get().strip()
        except ValueError:
            messagebox.showerror("Diagnóstico", "A porta deve ser um número inteiro.")
            return

        def done(value, error):
            if error:
                messagebox.showerror("Diagnóstico", str(error))
            else:
                messagebox.showinfo("Diagnóstico da conexão", value)
                self.status.set(value.splitlines()[-1])

        self.task(lambda: network_diagnostics(host, port, cafile), done)

    def _tab_changed(self, _event=None):
        current = self.book.select()
        if current == str(self.settings_tab) and not self._settings_unlocked:
            # Devolve imediatamente para Cadastro antes de solicitar a senha.
            # Também protege acessos por teclado e seleções programáticas.
            self.book.select(self.registration_tab)
            if not self._settings_auth_prompt_queued:
                self._settings_auth_prompt_queued = True
                self.after_idle(self._request_settings_password)
            return
        if current == str(self.history_tab):
            self._settings_unlocked = False
            self.refresh_history()
        if current == str(self.registration_tab):
            self._settings_unlocked = False
            if not self.register_button.winfo_manager():
                self.register_button.pack(side="right", padx=(8, 18), pady=8)
            if not self.delivery_button.winfo_manager():
                self.delivery_button.pack(side="right", padx=(8, 0), pady=8)
        else:
            self.register_button.pack_forget()
            self.delivery_button.pack_forget()
        modern_ui.update_navigation(self)

    def _request_settings_password(self):
        self._settings_auth_prompt_queued = False
        if not self.winfo_exists():
            return
        candidate = simpledialog.askstring(
            "Configurações protegidas", "Digite a senha para acessar Configurações:",
            parent=self, show="*")
        if candidate is None:
            return
        if not verify_settings_password(candidate):
            messagebox.showerror("Acesso negado", "Senha incorreta.", parent=self)
            return
        self._settings_unlocked = True
        self.book.select(self.settings_tab)

    def _startup_sync(self):
        if self.busy:
            self.after(10000, self._startup_sync)
        elif self.key and self.secret:
            self.connect(silent=True)
        else:
            self.status.set("Configure a conexão OpenAPI em Configurações.")

    def _apply_live_groups(self, groups: list[str]) -> bool:
        """Sincroniza grupos reais; preferência salva fixa a seleção neste caixa."""
        groups = selectable_visitor_groups(groups)
        if not groups:
            raise ValueError("Nenhum grupo Visitor elegível foi encontrado no HikCentral.")
        previous = self.group.get()
        self.connected_groups = groups
        self.groups_box["values"] = groups
        self.preferred_box.configure(
            values=[PREFERRED_AUTOMATIC, *groups], state="readonly")
        self._preferred_groups_synced = True
        preferred = self.settings.get("preferred_group", "")
        if preferred:
            # Preferência explícita funciona como trava administrativa do caixa.
            # Não aplicar fallback em grupo inexistente: impedir venda incorreta.
            self.s_preferred_group.set(preferred)
            self.groups_box.configure(state="disabled")
            self.group.set(preferred if preferred in groups else "")
            self._force_preferred_on_sync = False
            return preferred in groups
        self.s_preferred_group.set(PREFERRED_AUTOMATIC)
        self.groups_box.configure(state="readonly")
        if self._force_preferred_on_sync or previous not in groups:
            self.group.set(initial_visitor_group(groups))
        self._force_preferred_on_sync = False
        return True

    def connect(self, silent=False):
        # Testes manuais usam os campos atuais, mesmo antes de clicar em Salvar.
        if not silent and not self.save_config(silent=True):
            return
        if not self.key or not self.secret:
            self.verified = False
            self.status.set("Configure AppKey e AppSecret para sincronizar.")
            return

        def work():
            client = self.client()
            levels = client.access_levels()
            if not levels:
                raise RuntimeError("HikCentral respondeu, mas não retornou níveis Visitor type=2.")
            groups = selectable_visitor_groups(client.visitor_groups())
            if not groups:
                raise RuntimeError(
                    "A API não retornou grupos disponíveis além de Visitors. "
                    "Cadastro bloqueado; confira os grupos no HikCentral.")
            return groups, levels

        def finish(value, error):
            if error:
                self.verified = False
                self.connected_groups = []
                self.connection_label_var.set("Sem conexão")
                self.groups_box["values"] = ()
                self.groups_box.configure(state="disabled")
                self.group.set("")
                self._preferred_groups_synced = False
                self.preferred_box.configure(values=(), state="disabled")
                self.s_preferred_group.set(PREFERRED_PENDING)
                self.status.set("Sincronização automática indisponível: " + str(error))
                if silent and self.key and self.secret:
                    # Reconecta sem exigir botão quando HikCentral volta a responder.
                    self.after(60000, self._startup_sync)
                if not silent:
                    messagebox.showerror("Conexão", str(error))
                return
            groups, levels = value
            ready = self._apply_live_groups(groups)
            self.verified = ready
            self.connection_label_var.set("Conectado" if ready else "Grupo indisponível")
            if not ready:
                preferred = self.settings.get("preferred_group", "")
                self.status.set(
                    f"Grupo preferencial '{preferred}' indisponível no HikCentral. "
                    "Cadastro bloqueado. Abra Configurações, escolha outro grupo válido e salve.")
                if not silent:
                    messagebox.showwarning("Grupo indisponível", self.status.get())
                return
            self.status.set(f"Conectado: {len(groups)} grupos e {len(levels)} níveis Visitor.")
            if not silent:
                self.book.select(self.registration_tab)
            # Retomada segura de operações incompletas, sem botão na interface.
            self.after(80, self._auto_resume_pending)

        self.task(work, finish)

    def use_photo(self, source):
        try:
            raw = Path(source).read_bytes() if isinstance(source, (str, Path)) else source.read()
            self.photo_data = prepare_face_jpeg(raw)
            width = max(100, self.photo_widget.winfo_width() - 2)
            height = max(100, self.photo_widget.winfo_height() - 2)
            image = face_cover_preview(self.photo_data, width, height)
            self.preview = ImageTk.PhotoImage(image)
            self.photo_widget.configure(image=self.preview, text="")
            self.status.set("Foto selecionada; não armazenada permanentemente.")
            return True
        except Exception as exc:
            messagebox.showerror("Imagem", str(exc))
            return False

    def browse_photo(self):
        file = filedialog.askopenfilename(filetypes=[("Fotos", "*.jpg *.jpeg *.png *.bmp")])
        if file:
            self.use_photo(file)

    def camera(self):
        """Reutiliza a captura facial guiada sem alterar cadastro/NFC/HikCentral."""
        try:
            from face_capture_ui import open_face_capture
            open_face_capture(self)
        except Exception as exc:
            messagebox.showerror("Webcam", str(exc), parent=self)

    def scan(self):
        def work():
            names = list_readers()
            if not names:
                raise RuntimeError("Nenhum leitor PC/SC encontrado. Conecte o ACR122U.")
            reader, uid = read_uid(names[0])
            return reader, uid, to_card_number(uid, self.settings["card_format"])
        def finish(value, error):
            if error:
                messagebox.showerror("Leitor NFC", str(error))
                return
            reader, uid, number = value
            self.card.set(number)
            self.uid.set("Pulseira lida")
            self.status.set("Pulseira lida; número convertido preenchido.")
        self.status.set("Aproxime a pulseira do leitor NFC.")
        self.task(work, finish)

    def register(self):
        if load_pending():
            messagebox.showwarning("Cadastro em recuperação",
                                   "O cadastro anterior ainda não foi concluído ou conciliado. "
                                   "A recuperação automática preserva os dados existentes "
                                   "para evitar registros duplicados.")
            self._auto_resume_pending()
            return
        if not self.verified:
            messagebox.showwarning("Verificação", "Aguardando a sincronização automática do HikCentral.")
            return
        try:
            group = validate_visitor_group_selection(
                self.group.get(), self.connected_groups,
                self.settings.get("preferred_group", ""))
            # O grupo define um único nível Visitor autorizado nas Configurações.
            # A API valida que o nível pertence a type=2, nunca a funcionários.
            requested_levels = visitor_levels_for_group(
                group, self.connected_groups, self.settings["group_access"],
                self.settings.get("preferred_group", ""))
            ticket = self.settings["tickets"][self.ticket.get()]
            start = parse_local(self.start.get())
            end = parse_local(self.end.get())
            validate_ticket_period(start, end, ticket)
            require_confirmed_daily_schedule(
                requested_levels, ticket, self.settings.get("schedule_windows", {}))
            obj = Visitor(self.first.get(), self.last.get(), group, self.photo_data,
                          self.card.get().strip(), start, end, self.reservation.get())
            payload = registration_payload(obj, self.settings.get("valid_times", 0))
            expect_face, expect_card = bool(obj.photo_jpeg), bool(obj.card_number.strip())
        except Exception as exc:
            messagebox.showerror("Dados", str(exc))
            return

        self._recovery_attempts = 0

        def preflight():
            selected, door_ids = self.client().resolve_levels(requested_levels)
            verify_visitor_access_plan(selected, door_ids, requested_levels)
            return selected, door_ids

        def preflight_done(value, error):
            if error:
                self.status.set(str(error))
                messagebox.showerror("Prévia de acesso", str(error))
                return
            selected, door_ids = value
            door_names = {}
            level_lines = []
            for level in selected:
                level_lines.append(f"• {level['name']} (ID {level['id']})")
                for entry in level.get("elements", []):
                    element = entry.get("Element") or {}
                    element_id = str(element.get("ID") or "")
                    if element_id:
                        name = (element.get("BaseInfo") or {}).get("Name") or element_id
                        door_names[element_id] = str(name)
            door_lines = [f"• {door_names.get(door_id, door_id)} (ID {door_id})" for door_id in door_ids]
            credentials = " e ".join(label for present, label in
                                     ((expect_face, "reconhecimento facial"),
                                      (expect_card, "pulseira/cart\u00e3o")) if present)
            confirmation = (
                f"Visitante: {obj.first_name.strip()} {obj.last_name.strip()}\n"
                f"Grupo: {group}\n"
                f"Entrada: {obj.arrival.strftime('%d/%m/%Y %H:%M %Z')}\n"
                f"Sa\u00edda: {obj.departure.strftime('%d/%m/%Y %H:%M %Z')}\n"
                f"Janela diária autorizada: {ticket.get('checkin', '09:00')}-"
                f"{ticket['checkout']} (validada pela administração nos níveis)\n\n"
                "N\u00edveis Visitor que ser\u00e3o associados:\n" + "\n".join(level_lines) +
                "\n\nCatracas alcan\u00e7adas por esses n\u00edveis:\n" + "\n".join(door_lines) +
                f"\n\nCredencial(is) a enviar: {credentials}. " +
                ("Confirme que a pulseira de teste n\u00e3o est\u00e1 vinculada a outro visitante.\n\n"
                 if expect_card else "\n\n") +
                "Ao confirmar, o aplicativo criará a reserva e registrará o check-in automaticamente.\n\n"
                "Prosseguir?"
            )
            if not messagebox.askyesno("Confirmar níveis e catracas", confirmation, parent=self):
                self.status.set("Cadastro cancelado antes do envio.")
                return

            def work():
                client = self.client()
                # Falha fechada: se o POST for aceito mas a resposta se perder,
                # a recepção terá de conciliar a reserva antes de reenviá-la.
                mark_appointment_inflight()
                appointment_id, visitor_id = client.create_appointment(payload, selected)
                # Checkpoint immediately after the reservation to prevent duplicates.
                access_ids = [level["id"] for level in selected]
                save_pending(visitor_id, access_ids, appointment_id=appointment_id,
                             door_ids=door_ids, registered=False,
                             expect_face=expect_face, expect_card=expect_card,
                             entry_at=payload["visitStartTime"],
                             exit_at=payload["visitEndTime"],
                             access_names=[level["name"] for level in selected])
                registration_record_id = client.register_visitor(payload, selected,
                                                                  appointment_id, visitor_id)
                mark_registered(registration_record_id)
                for level_id in access_ids:
                    client.assign_access(visitor_id, level_id)
                    mark_assigned(level_id)
                client.apply(visitor_id, door_ids)
                save_last_delivery(visitor_id, door_ids,
                                   expect_face=expect_face, expect_card=expect_card,
                                   entry_at=payload["visitStartTime"],
                                   exit_at=payload["visitEndTime"],
                                   appointment_id=appointment_id,
                                   registration_record_id=registration_record_id)
                clear_pending()
                try:
                    save_registration({
                        "visitor_id": visitor_id, "appointment_id": appointment_id,
                        "registration_record_id": registration_record_id,
                        "first": obj.first_name, "last": obj.last_name,
                        "group": group, "reservation": obj.reservation,
                        "door_ids": door_ids, "access_names": [level["name"] for level in selected],
                        "entry_at": payload["visitStartTime"], "exit_at": payload["visitEndTime"],
                        "expect_face": expect_face, "expect_card": expect_card,
                        "state": "em consulta", "checkin_state": "não verificado"
                    })
                except Exception:
                    pass  # remote registration was already completed; do not retry creation
                try:
                    report = self._poll_delivery(client, visitor_id, door_ids,
                                                 expect_face=expect_face, expect_card=expect_card)
                    report_error = ""
                    try:
                        set_history_status(visitor_id, state=report["state"])
                    except Exception:
                        pass
                except Exception as exc:
                    report, report_error = None, str(exc)
                return {"visitor_id": visitor_id, "appointment_id": appointment_id,
                        "registration_record_id": registration_record_id,
                        "report": report, "report_error": report_error}

            def finish(result, error):
                if error:
                    pending = load_pending()
                    if pending:
                        if pending.get("stage") == "appointment_inflight":
                            advice = ("O envio da reserva pode ter sido aceito, mas a resposta é incerta. "
                                      "Antes de repetir, consulte Visitor > Visitor Reservation no HikCentral. "
                                      "Nenhuma nova reserva será criada automaticamente.")
                        elif not pending.get("registered", True):
                            advice = ("Existe uma reserva, mas o check-in não está confirmado. "
                                      "Confira o estado no HikCentral antes de retomar ou repetir.")
                        else:
                            advice = "A recuperação automática verificará os acessos já autorizados."
                        known_id = pending.get("visitor_id") or "não recebido"
                        messagebox.showwarning("Cadastro parcial",
                            f"Visitor ID: {known_id}.\n{error}\n\n{advice}")
                    else:
                        messagebox.showerror("Resultado incerto",
                            f"{error}\nConfira no HikCentral antes de tentar novamente.")
                    self.status.set("Operação não concluída; verificando recuperação automática.")
                    if pending:
                        self.after(4000, self._auto_resume_pending)
                    return
                report = result["report"]
                if report:
                    detail = "\n".join(f"• {d['name']}: face {d['faceState']}; pulseira {d['credentialState']}"
                                        for d in report["doors"])
                    text = (f"Visitor ID: {result['visitor_id']}\nReserva: {result['appointment_id']}\n"
                            f"Registro de check-in (ID devolvido pela API): {result['registration_record_id']}\n"
                            "Para comprovar o registro na base Visitor, use Verificar última entrega.\n\n"
                            f"{report['message']}\n{detail}")
                    if report["state"] == "confirmed":
                        messagebox.showinfo("Entrega confirmada", text)
                    elif report["state"] == "failed":
                        messagebox.showwarning("Falha de entrega", text)
                    else:
                        messagebox.showwarning("Entrega pendente", text +
                                               "\n\nUse Verificar última entrega para consultar novamente.")
                    self.status.set(f"Cadastro concluído; entrega {report['state']}.")
                else:
                    messagebox.showwarning("Entrega não verificada",
                        f"Visitor ID: {result['visitor_id']}\nSolicitação de reaplicação enviada.\n"
                        f"A consulta de entrega falhou: {result['report_error']}\n\n"
                        "Use Verificar última entrega para repetir somente a consulta.")
                    self.status.set("Cadastro concluído; entrega ainda não verificada.")
                for var in (self.first, self.last, self.card, self.reservation):
                    var.set("")
                self.uid.set("UID não lido")
                # Próxima venda respeita grupo bloqueado ou escolha livre do caixa.
                preferred = self.settings.get("preferred_group", "")
                self.group.set(
                    preferred if preferred in self.connected_groups
                    else (self.connected_groups[0] if not preferred and self.connected_groups else ""))
                self.photo_data, self.preview = b"", None
                self.photo_widget.configure(image="", text="SEM FOTO")

            # Spinner visible only after the operator answered SIM to the preview.
            self.task(work, finish, registration_loading=True)

        self.status.set("Consultando níveis e catracas para prévia; nenhum cadastro será enviado ainda.")
        self.task(preflight, preflight_done)


    def refresh_history(self):
        try:
            rows = list_history()
            self.history_table.delete(*self.history_table.get_children())
            for record in rows:
                vid = record["visitor_id"]
                self.history_table.insert("", "end", iid=vid, values=(
                    record.get("entry_at", "")[:16].replace("T", " "),
                    " ".join(filter(None, (record.get("first"), record.get("last")))),
                    record.get("group", ""),
                    ", ".join(record.get("access_names", [])),
                    record.get("checkin_state", "não verificado"),
                    record.get("state", "não verificada")))
            self.history_status.set(f"{len(rows)} cadastros armazenados neste computador.")
        except Exception as exc:
            self.history_status.set("Não foi possível abrir histórico: " + str(exc))

    def _history_selection(self, _event=None):
        chosen = self.history_table.selection()
        if chosen:
            record = get_history(chosen[0])
            if record:
                self.history_status.set(
                    f"Visitor ID: {record['visitor_id']} | Check-in: {record.get('checkin_state', 'não verificado')} | "
                    f"Catracas: {record.get('state', 'não verificada')}")

    def _selected_history(self):
        chosen = self.history_table.selection()
        if not chosen:
            messagebox.showinfo("Histórico", "Selecione um cadastro primeiro.")
            return None
        return get_history(chosen[0])

    def verify_history_selection(self):
        record = self._selected_history()
        if not record:
            return
        def work():
            client = self.client()
            checkin = client.find_existing_registration(
                record["visitor_id"], record["entry_at"], record["exit_at"])
            report = client.delivery_report(
                record["visitor_id"], record["door_ids"],
                expect_face=record.get("expect_face", True),
                expect_card=record.get("expect_card", True))
            return checkin, report
        def finish(value, error):
            if error:
                self.history_status.set("Consulta não concluída: " + str(error))
                return
            checkin, report = value
            checkin_state = "confirmado" if checkin else "não localizado"
            set_history_status(record["visitor_id"], state=report["state"],
                               checkin_state=checkin_state)
            self.refresh_history()
            self.history_status.set(f"Check-in: {checkin_state}. Entrega: {report['state']}. {report['message']}")
        self.task(work, finish)

    def resend_history_selection(self):
        record = self._selected_history()
        if not record:
            return
        if load_pending():
            messagebox.showwarning("Reenvio bloqueado", "Existe operação pendente. Concilie primeiro.")
            return
        try:
            start = datetime.fromisoformat(record["entry_at"])
            end = datetime.fromisoformat(record["exit_at"])
            if start.tzinfo is None or end.tzinfo is None or not start <= datetime.now(TZ) < end:
                raise ValueError("Visita fora do período autorizado; reenvio bloqueado.")
        except (KeyError, ValueError) as exc:
            messagebox.showwarning("Reenvio bloqueado", str(exc))
            return
        if not messagebox.askyesno(
            "Confirmar reenvio",
            "Reaplicar ao Visitor ID existente, SEM criar nova reserva ou visitante?"):
            return
        def work():
            client = self.client()
            checkin = client.find_existing_registration(
                record["visitor_id"], record["entry_at"], record["exit_at"])
            if not checkin:
                raise ValueError("Check-in não confirmado; nenhum reenvio realizado.")
            names = record["access_names"]
            selected, doors = client.resolve_levels(names)
            verify_visitor_access_plan(selected, doors, names)
            if set(doors) != set(record["door_ids"]):
                raise ValueError("Pontos do nível alterados; reenvio bloqueado.")
            client.apply(record["visitor_id"], doors)
            return client.delivery_report(
                record["visitor_id"], doors,
                expect_face=record.get("expect_face", True),
                expect_card=record.get("expect_card", True))
        def finish(result, error):
            if error:
                self.history_status.set("Reenvio não confirmado: " + str(error))
                messagebox.showwarning("Reenvio", str(error))
                return
            try:
                set_history_status(record["visitor_id"], state=result["state"], checkin_state="confirmado")
                self.refresh_history()
            except Exception:
                self.history_status.set("Reenvio enviado, mas o histórico local não foi atualizado.")
            messagebox.showinfo("Reenvio", "Solicitação enviada ao visitante existente. Entrega: " + result["state"])
        self.task(work, finish)

    def view_history_selection(self, _event=None):
        record = self._selected_history()
        if record:
            from visitor_editor import open_visitor_editor
            open_visitor_editor(self, record)

    def verify_last_delivery(self):
        try:
            data = load_last_delivery()
            if not data:
                messagebox.showinfo("Entrega", "Ainda não há cadastro recente para consultar.")
                return
        except Exception as exc:
            messagebox.showerror("Entrega", str(exc))
            return

        def work():
            client = self.client()
            report = client.delivery_report(
                data["visitor_id"], data["door_ids"],
                expect_face=data.get("expect_face", True),
                expect_card=data.get("expect_card", True))
            registration_state = "não verificado (cadastro anterior sem datas)"
            registration_error = ""
            if data.get("entry_at") and data.get("exit_at"):
                try:
                    record = client.find_existing_registration(
                        data["visitor_id"], data["entry_at"], data["exit_at"])
                    if record:
                        registration_state = "confirmado pela consulta de check-in"
                    else:
                        registration_state = "não localizado na consulta de check-in"
                except Exception:
                    registration_state = "consulta indisponível"
                    registration_error = "Verifique o módulo Visitor no HikCentral."
            return report, registration_state, registration_error

        def finish(result, error):
            if error:
                self.status.set("Não foi possível consultar a entrega.")
                messagebox.showerror("Entrega", str(error))
                return
            report, registration_state, registration_error = result
            lines = [
                f"Visitor ID: {data['visitor_id']}",
                f"Check-in: {registration_state}",
                report["message"]
            ]
            if registration_error:
                lines.append(registration_error)
            lines.extend(f"• {door['name']} (ID {door['id']}): face {door['faceState']}; "
                         f"pulseira {door['credentialState']}" for door in report["doors"])
            self.status.set(f"Última entrega: {report['state']}; check-in: {registration_state}.")
            if report["state"] == "confirmed" and registration_state.startswith("confirmado"):
                messagebox.showinfo("Cadastro e entrega consultados", "\n".join(lines))
            else:
                lines.append("A entrega da credencial não comprova sozinha o registro Visitor. "
                             "Confira Visitor > Check-In/Out ou Visitor Information.")
                messagebox.showwarning("Verificação incompleta", "\n".join(lines))

        self.task(work, finish)

    @staticmethod
    def _poll_delivery(client, visitor_id, door_ids, *, expect_face=True, expect_card=True):
        report = client.delivery_report(visitor_id, door_ids,
                                        expect_face=expect_face, expect_card=expect_card)
        for _ in range(2):
            if report["state"] != "queued":
                break
            time.sleep(2)
            report = client.delivery_report(visitor_id, door_ids,
                                            expect_face=expect_face, expect_card=expect_card)
        return report

    def _auto_resume_pending(self):
        """Retoma operações confirmadas; não repete check-in com resultado incerto."""
        data = load_pending()
        if not data or not self.verified:
            return
        if data.get("stage") == "appointment_inflight":
            self.status.set(
                "Reserva com resultado incerto no HikCentral. Confira o histórico de reservas "
                "antes de autorizar novo cadastro. Não reenviar automaticamente.")
            return
        if self.busy:
            self.after(7000, self._auto_resume_pending)
            return
        if not data.get("door_ids"):
            self.status.set("Cadastro anterior exige conciliação: IDs das catracas indisponíveis.")
            return

        def work():
            client = self.client()
            pending = load_pending()
            # Nunca recuperar pendência desconhecida com permissões presumidas.
            approved_names = pending.get("access_names")
            if not isinstance(approved_names, list) or len(approved_names) != 1:
                return {"unresolved": "Pendência antiga sem vínculo de acesso comprovado. "
                                      "Verifique manualmente o HikCentral; nada foi reaplicado."}
            selected, expected_doors = client.resolve_levels(approved_names)
            verify_visitor_access_plan(selected, expected_doors, approved_names)
            if (pending.get("access_ids") != [str(level["id"]) for level in selected]
                    or set(pending.get("door_ids", [])) != set(expected_doors)):
                return {"unresolved": "Pendência com nível/pontos diferentes do registro original. "
                                      "Revisão manual necessária; nenhum acesso foi alterado."}
            if not pending.get("registered", False):
                entry_at, exit_at = pending.get("entry_at"), pending.get("exit_at")
                if not entry_at or not exit_at:
                    return {"unresolved": "Cadastro anterior sem intervalo para conferir check-in."}
                record_id = client.find_existing_registration(
                    pending["visitor_id"], entry_at, exit_at)
                if not record_id:
                    return {"unresolved": "Check-in não confirmado na consulta de leitura. "
                                          "Revisar a visita no HikCentral; nenhuma nova reserva foi criada."}
                mark_registered(record_id)
                pending = load_pending()
            for level in pending["access_ids"]:
                if level not in pending["assigned"]:
                    client.assign_access(pending["visitor_id"], level)
                    mark_assigned(level)
            client.apply(pending["visitor_id"], pending["door_ids"])
            expect_face, expect_card = pending.get("expect_face", True), pending.get("expect_card", True)
            save_last_delivery(pending["visitor_id"], pending["door_ids"],
                               expect_face=expect_face, expect_card=expect_card,
                               entry_at=pending.get("entry_at", ""),
                               exit_at=pending.get("exit_at", ""),
                               appointment_id=pending.get("appointment_id", ""),
                               registration_record_id=pending.get("registration_record_id", ""))
            clear_pending()
            try:
                report = self._poll_delivery(client, pending["visitor_id"], pending["door_ids"],
                                             expect_face=expect_face, expect_card=expect_card)
                status = report["state"]
            except Exception:
                status = "não verificada"
            return {"visitor_id": pending["visitor_id"], "state": status}

        def finish(value, error):
            if error:
                self.status.set("Recuperação pendente: " + str(error))
            elif "unresolved" in value:
                self.status.set(value["unresolved"])
            else:
                self._recovery_attempts = 0
                self.status.set("Cadastro pendente recuperado automaticamente; entrega " + value["state"] + ".")
                return
            self._recovery_attempts += 1
            if self._recovery_attempts < 3:
                self.after(15000, self._auto_resume_pending)
            else:
                self.status.set("Cadastro ainda pendente: confira o registro no HikCentral. "
                                "Nenhum cadastro duplicado será criado.")

        self.status.set("Recuperando cadastro anterior com segurança...")
        self.task(work, finish)


def readonly_api_check(report_path: Path) -> int:
    """Read-only smoke check for the frozen EXE; output contains no credentials or PII."""
    try:
        import cv2
        opencv_imported = True
        opencv_version = cv2.__version__
    except Exception as exc:
        opencv_imported = False
        opencv_version = type(exc).__name__
    try:
        from smartcard.System import readers
        pcsc_library_imported = True
        try:
            reader_count = len(readers())
            pcsc_status = "reader_found" if reader_count else "no_reader"
        except Exception as exc:
            reader_count = None
            pcsc_status = ("service_stopped" if "0x8010001D" in str(exc)
                           else "reader_service_error")
    except Exception as exc:
        pcsc_library_imported = False
        reader_count = None
        pcsc_status = type(exc).__name__

    try:
        settings = load_settings()
        key, secret = load_api_keys()
        client = HikCentral(settings["host"], settings["port"], key, secret,
                            settings["ca_file"], pinned_sha256=settings.get("cert_pin_sha256", ""))
        levels = client.access_levels()
        groups = client.visitor_groups()
        if not groups or not levels:
            raise RuntimeError("OpenAPI não retornou grupos e níveis Visitor.")
        mapped_groups = {}
        for group in selectable_visitor_groups(groups):
            requested = settings.get("group_access", {}).get(group, [])
            if not isinstance(requested, list) or len(requested) != 1:
                mapped_groups[group] = {"mapping_valid": False}
                continue
            selected, doors = client.resolve_levels(requested)
            verify_visitor_access_plan(selected, doors, requested)
            mapped_groups[group] = {
                "mapping_valid": True,
                "level_name": selected[0]["name"],
                "point_count": len(doors),
            }
        preferred = settings.get("preferred_group", "")
        mapping_ready = (
            bool(mapped_groups.get(preferred, {}).get("mapping_valid"))
            if preferred else any(x["mapping_valid"] for x in mapped_groups.values())
        )
        runtime_ok = opencv_imported and pcsc_library_imported
        report = {
            "ok": runtime_ok and mapping_ready,
            "version": APP_VERSION,
            "endpoint": f"{settings['host']}:{settings['port']}",
            "certificate_pin_configured": bool(settings.get("cert_pin_sha256")),
            "visitor_group_count": len(groups),
            "visitor_level_count": len(levels),
            "visitor_level_type": 2,
            "configured_group_mappings": mapped_groups,
            "mapped_group_count": sum(1 for m in mapped_groups.values() if m["mapping_valid"]),
            "configured_preferred_group_ready": mapping_ready,
            "read_only": True,
            "check_in_real_record_verified": False,
            "effective_schedule_readback": "not_verified_by_this_API_check",
            "default_level_flag_readback": "not_verified_by_this_API_check",
            "openhikcentral_writes_performed": False,
            "opencv_imported": opencv_imported,
            "opencv_version": opencv_version,
            "pcsc_library_imported": pcsc_library_imported,
            "pcsc_status": pcsc_status,
            "pcsc_reader_count": reader_count,
            "checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
        }
        result = 0 if runtime_ok and mapping_ready else 1
    except Exception as exc:
        report = {"ok": False, "version": APP_VERSION, "error": str(exc)}
        result = 1
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def offline_startup_smoke_check(report_path: Path) -> int:
    """Startup test of real import graph, without HikCentral or any user data.

    Intended to run on the actual frozen Windows EXE before publishing it.
    """
    from core import (default_settings, VALE_VISITOR_LEVELS,
                      VALE_VISITOR_POINT_COUNTS, VALE_VISITOR_SCHEDULE)
    report = {"kind": "offline_startup_smoke", "version": APP_VERSION,
              "network_used": False, "visitor_created": False, "ok": False}
    try:
        conf = default_settings()
        for group, level in VALE_VISITOR_LEVELS.items():
            if conf["group_access"].get(group) != [level]:
                raise ValueError("Configuração padrão de grupo inválida.")
            if conf["schedule_windows"].get(level) != VALE_VISITOR_SCHEDULE:
                raise ValueError("Agenda diária padrão inválida.")
            # Fake identifiers only; no query or operation against HikCentral.
            points = [f"offline-{index}" for index in range(VALE_VISITOR_POINT_COUNTS[level])]
            verify_visitor_access_plan(
                [{"id": "offline-test", "name": level}], points, [level])
        # Open and close actual Tk desktop interface with isolated defaults.
        # No local credentials, network, or persisted files are accessed here.
        from core import default_settings as _defaults
        original_settings, original_keys = globals()["load_settings"], globals()["load_api_keys"]
        root = None
        try:
            globals()["load_settings"] = lambda: _defaults()
            globals()["load_api_keys"] = lambda: ("", "")
            root = ValeApp()
            root.withdraw()
            root.update_idletasks()  # no timer callbacks / OpenAPI sync
            if not (root.register_button.winfo_exists()
                    and root.save_button.winfo_exists()
                    and root.groups_box.winfo_exists()):
                raise RuntimeError("Os componentes principais não foram inicializados.")
            report["gui_created"] = True
        finally:
            if root is not None:
                root.destroy()
            globals()["load_settings"], globals()["load_api_keys"] = original_settings, original_keys
        report["ok"] = True
    except Exception as exc:
        report["error_type"] = type(exc).__name__
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        raise SystemExit(offline_startup_smoke_check(Path(sys.argv[2])))
    if len(sys.argv) == 3 and sys.argv[1] == "--check-openapi":
        raise SystemExit(readonly_api_check(Path(sys.argv[2])))
    ValeApp().mainloop()
