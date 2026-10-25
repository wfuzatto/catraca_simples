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
                  initial_visitor_group,
                  save_api_keys, parse_local, end_for_ticket, registration_payload,
                  prepare_face_jpeg,
                  parse_ticket_lines, parse_access_lines, load_pending, save_pending,
                  mark_assigned, mark_registered, clear_pending,
                  save_last_delivery, load_last_delivery)
from devices import list_readers, read_uid, to_card_number
from hikcentral import HikCentral, network_diagnostics, read_cert_sha256, normalize_pin
from settings_auth import verify_settings_password

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
        top = tk.Frame(self, bg="#143c5d")
        top.pack(fill="x")
        tk.Label(top, text="VALE  |  Cadastro Facial e NFC", bg="#143c5d", fg="white",
                 font=("Segoe UI", 18, "bold")).pack(side="left", padx=18, pady=15)
        tk.Label(top, text="Integração local HikCentral", bg="#143c5d",
                 fg="#c7dce9").pack(side="right", padx=18)
        # Rodapé fixo: o botão nunca desaparece com a altura do Notebook.
        footer = tk.Frame(self, bg="#e7eef4", height=65)
        footer.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="Verificando configurações do HikCentral...")
        self.register_button = tk.Button(
            footer, text="CADASTRAR VISITANTE", command=self.register,
            font=("Segoe UI", 11, "bold"), fg="white", bg="#17734f",
            activebackground="#135b3e", activeforeground="white",
            relief="raised", bd=1, padx=20, pady=11, cursor="hand2")
        self.register_button.pack(side="right", padx=(8, 18), pady=8)
        self.delivery_button = ttk.Button(
            footer, text="Verificar última entrega", command=self.verify_last_delivery)
        self.delivery_button.pack(side="right", padx=(8, 0), pady=8)
        tk.Label(footer, textvariable=self.status, bg="#e7eef4",
                 fg="#244c63", anchor="w", wraplength=580,
                 justify="left").pack(side="left", fill="x", expand=True, padx=15)

        self.book = ttk.Notebook(self)
        self.book.pack(fill="both", expand=True, padx=13, pady=10)
        self.registration_tab = ttk.Frame(self.book, padding=12)
        self.settings_tab = ttk.Frame(self.book, padding=12)
        self.book.add(self.registration_tab, text="Cadastro")
        self.book.add(self.settings_tab, text="Configurações")
        self._registration()
        self._settings()
        self.book.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.status.set("Sincronização automática ao iniciar..." if self.key and self.secret
                        else "Configure AppKey/AppSecret em Configurações.")

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
        # Não preencher o select com grupos presumidos ou valores antigos.
        self.s_preferred_group.set(PREFERRED_PENDING)
        self.preferred_box.configure(values=(), state="disabled")
        self._preferred_groups_synced = False
        self.tickets_text.delete("1.0", tk.END)
        self.tickets_text.insert("1.0", "\n".join(
            f"{name};{opt['days']};{opt['checkout']}" for name, opt in s["tickets"].items()))
        self.access_text.delete("1.0", tk.END)
        self.access_text.insert("1.0", "\n".join(
            f"{name}={','.join(levels)}" for name, levels in s["group_access"].items()))
        self.ticket_box["values"] = list(s["tickets"])
        self.ticket.set(next(iter(s["tickets"])))
        self.start.set(datetime.now(TZ).strftime("%d/%m/%Y %H:%M"))
        self.calculate()

    def calculate(self):
        """Recálculo automático apenas quando data de entrada e ingresso são válidos."""
        try:
            result = end_for_ticket(parse_local(self.start.get()),
                                    self.settings["tickets"][self.ticket.get()])
        except (KeyError, ValueError):
            # Enquanto o operador digita, mantém a saída anterior até a entrada estar válida.
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
                "tickets": parse_ticket_lines(self.tickets_text.get("1.0", tk.END)),
                "group_access": parse_access_lines(self.access_text.get("1.0", tk.END))
            }
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
            messagebox.showerror("Configuração", str(exc))
            return False

    def client(self):
        s = self.settings
        return HikCentral(s["host"], s["port"], self.key, self.secret, s["ca_file"],
                          pinned_sha256=s.get("cert_pin_sha256", ""))

    def task(self, work, finish):
        if self.busy:
            messagebox.showwarning("Em andamento", "Operação anterior em andamento.")
            return
        self.busy = True
        self.status.set("Consultando HikCentral...")
        def run():
            try:
                value, error = work(), None
            except Exception as exc:
                value, error = None, exc
            self.after(0, lambda: done(value, error))
        def done(value, error):
            self.busy = False
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
        if current == str(self.registration_tab):
            self._settings_unlocked = False
            if not self.register_button.winfo_manager():
                self.register_button.pack(side="right", padx=(8, 18), pady=8)
            if not self.delivery_button.winfo_manager():
                self.delivery_button.pack(side="right", padx=(8, 0), pady=8)
        else:
            self.register_button.pack_forget()
            self.delivery_button.pack_forget()

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

    def _apply_live_groups(self, groups: list[str]) -> None:
        """Usa exclusivamente os grupos sincronizados; mantém a troca manual no cadastro."""
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
        self.s_preferred_group.set(
            preferred if preferred in groups else PREFERRED_AUTOMATIC)
        if self._force_preferred_on_sync or previous not in groups:
            self.group.set(initial_visitor_group(groups, preferred))
        self._force_preferred_on_sync = False

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
                self.groups_box["values"] = ()
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
            self._apply_live_groups(groups)
            self.verified = True
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
            img = Image.open(io.BytesIO(self.photo_data))
            thumb = img.copy()
            thumb.thumbnail((145, 125))
            self.preview = ImageTk.PhotoImage(thumb)
            self.photo_widget.configure(image=self.preview, text="", width=145, height=125)
            self.status.set("Foto selecionada; não armazenada permanentemente.")
        except Exception as exc:
            messagebox.showerror("Imagem", str(exc))

    def browse_photo(self):
        file = filedialog.askopenfilename(filetypes=[("Fotos", "*.jpg *.jpeg *.png *.bmp")])
        if file:
            self.use_photo(file)

    def camera(self):
        try:
            import cv2
            cam = cv2.VideoCapture(int(self.settings["camera_index"]), cv2.CAP_DSHOW)
            if not cam.isOpened():
                raise RuntimeError("Webcam indisponível.")
            window = tk.Toplevel(self)
            window.title("Captura da foto facial")
            window.transient(self)
            window.resizable(False, False)
            video = tk.Label(window, text="Abrindo câmera…", width=72, height=28, bg="#17212b", fg="white")
            video.pack(padx=12, pady=12)
            controls = ttk.Frame(window)
            controls.pack(fill="x", padx=12, pady=(0, 12))
            state = {"frame": None, "captured": None, "image": None, "closed": False}

            def close():
                if state["closed"]:
                    return
                state["closed"] = True
                cam.release()
                if window.winfo_exists():
                    window.destroy()

            def show_frame(frame):
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                preview = Image.fromarray(rgb)
                preview.thumbnail((720, 480))
                state["image"] = ImageTk.PhotoImage(preview)
                video.configure(image=state["image"], text="", width=720, height=480)

            def update_preview():
                if state["closed"] or not window.winfo_exists() or state["captured"] is not None:
                    return
                ok, frame = cam.read()
                if ok:
                    state["frame"] = frame.copy()
                    show_frame(frame)
                else:
                    video.configure(text="Câmera conectada, mas sem imagem.", image="")
                window.after(35, update_preview)

            def capture():
                if state["frame"] is None:
                    messagebox.showwarning("Webcam", "A câmera ainda não forneceu uma imagem.", parent=window)
                    return
                state["captured"] = state["frame"].copy()
                show_frame(state["captured"])
                capture_button.configure(state="disabled")
                retake_button.configure(state="normal")
                use_button.configure(state="normal")

            def retake():
                state["captured"] = None
                capture_button.configure(state="normal")
                retake_button.configure(state="disabled")
                use_button.configure(state="disabled")
                update_preview()

            def use_capture():
                if state["captured"] is None:
                    return
                ok, jpg = cv2.imencode(".jpg", state["captured"], [cv2.IMWRITE_JPEG_QUALITY, 94])
                if not ok:
                    messagebox.showerror("Webcam", "Não foi possível preparar a foto.", parent=window)
                    return
                try:
                    self.use_photo(io.BytesIO(jpg.tobytes()))
                finally:
                    close()

            capture_button = ttk.Button(controls, text="Capturar", command=capture)
            capture_button.pack(side="left")
            retake_button = ttk.Button(controls, text="Refazer", command=retake, state="disabled")
            retake_button.pack(side="left", padx=8)
            use_button = ttk.Button(controls, text="Usar foto", command=use_capture, state="disabled")
            use_button.pack(side="left")
            ttk.Button(controls, text="Cancelar", command=close).pack(side="right")
            window.protocol("WM_DELETE_WINDOW", close)
            update_preview()
        except Exception as exc:
            messagebox.showerror("Webcam", str(exc))

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
            group = self.group.get()
            if group not in self.connected_groups:
                raise ValueError("Grupo não confirmado pela API.")
            if group not in self.settings["group_access"]:
                raise ValueError("Configure níveis de acesso para este grupo.")
            requested_levels = self.settings["group_access"][group]
            if not requested_levels:
                raise ValueError("Este grupo ainda não tem níveis aprovados. Configure o mapeamento antes do cadastro.")
            obj = Visitor(self.first.get(), self.last.get(), group, self.photo_data,
                          self.card.get().strip(), parse_local(self.start.get()),
                          parse_local(self.end.get()).replace(second=59), self.reservation.get())
            payload = registration_payload(obj)
            expect_face, expect_card = bool(obj.photo_jpeg), bool(obj.card_number.strip())
        except Exception as exc:
            messagebox.showerror("Dados", str(exc))
            return

        self._recovery_attempts = 0

        def preflight():
            return self.client().resolve_levels(requested_levels)

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
                f"Sa\u00edda: {obj.departure.strftime('%d/%m/%Y %H:%M %Z')}\n\n"
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
                appointment_id, visitor_id = client.create_appointment(payload, selected)
                # Checkpoint immediately after the reservation to prevent duplicates.
                access_ids = [level["id"] for level in selected]
                save_pending(visitor_id, access_ids, appointment_id=appointment_id,
                             door_ids=door_ids, registered=False,
                             expect_face=expect_face, expect_card=expect_card,
                             entry_at=payload["visitStartTime"],
                             exit_at=payload["visitEndTime"])
                registration_record_id = client.register_visitor(payload, selected,
                                                                  appointment_id, visitor_id)
                mark_registered(registration_record_id)
                for level_id in access_ids:
                    client.assign_access(visitor_id, level_id)
                    mark_assigned(level_id)
                client.apply(visitor_id, door_ids)
                save_last_delivery(visitor_id, door_ids,
                                   expect_face=expect_face, expect_card=expect_card)
                clear_pending()
                try:
                    report = self._poll_delivery(client, visitor_id, door_ids,
                                                 expect_face=expect_face, expect_card=expect_card)
                    report_error = ""
                except Exception as exc:
                    report, report_error = None, str(exc)
                return {"visitor_id": visitor_id, "appointment_id": appointment_id,
                        "registration_record_id": registration_record_id,
                        "report": report, "report_error": report_error}

            def finish(result, error):
                if error:
                    pending = load_pending()
                    if pending:
                        if not pending.get("registered", True):
                            advice = ("A reserva foi criada, mas o check-in não está confirmado. "
                                      "Confira o estado no HikCentral antes de retomar ou repetir. "
                                      "Não faça uma segunda reserva.")
                        else:
                            advice = "A recuperação automática tentará reaplicar os acessos, sem criar outro visitante."
                        messagebox.showwarning("Cadastro parcial",
                            f"Visitante ID {pending['visitor_id']}.\n{error}\n\n{advice}")
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
                            f"Registro de check-in: {result['registration_record_id']}\n\n"
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
                # Próxima venda começa no grupo preferencial, sem impedir trocas manuais.
                self.group.set(initial_visitor_group(
                    self.connected_groups, self.settings.get("preferred_group", "")))
                self.photo_data, self.preview = b"", None
                self.photo_widget.configure(image="", text="SEM FOTO", width=20, height=7)

            self.task(work, finish)

        self.status.set("Consultando níveis e catracas para prévia; nenhum cadastro será enviado ainda.")
        self.task(preflight, preflight_done)

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
            return self.client().delivery_report(
                data["visitor_id"], data["door_ids"],
                expect_face=data.get("expect_face", True),
                expect_card=data.get("expect_card", True))

        def finish(report, error):
            if error:
                self.status.set("Não foi possível consultar a entrega.")
                messagebox.showerror("Entrega", str(error))
                return
            lines = [report["message"]]
            lines.extend(f"• {door['name']} (ID {door['id']}): face {door['faceState']}; "
                         f"pulseira {door['credentialState']}" for door in report["doors"])
            self.status.set(f"Última entrega: {report['state']}.")
            if report["state"] == "confirmed":
                messagebox.showinfo("Última entrega", "\n".join(lines))
            else:
                messagebox.showwarning("Última entrega", "\n".join(lines))

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
        if self.busy:
            self.after(7000, self._auto_resume_pending)
            return
        if not data.get("door_ids"):
            self.status.set("Cadastro anterior exige conciliação: IDs das catracas indisponíveis.")
            return

        def work():
            client = self.client()
            pending = load_pending()
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
                               expect_face=expect_face, expect_card=expect_card)
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
        runtime_ok = opencv_imported and pcsc_library_imported
        report = {
            "ok": runtime_ok,
            "version": APP_VERSION,
            "endpoint": f"{settings['host']}:{settings['port']}",
            "certificate_pin_configured": bool(settings.get("cert_pin_sha256")),
            "visitor_group_count": len(groups),
            "visitor_level_count": len(levels),
            "opencv_imported": opencv_imported,
            "opencv_version": opencv_version,
            "pcsc_library_imported": pcsc_library_imported,
            "pcsc_status": pcsc_status,
            "pcsc_reader_count": reader_count,
            "checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
        }
        result = 0 if runtime_ok else 1
    except Exception as exc:
        report = {"ok": False, "version": APP_VERSION, "error": str(exc)}
        result = 1
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check-openapi":
        raise SystemExit(readonly_api_check(Path(sys.argv[2])))
    ValeApp().mainloop()
