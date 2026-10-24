"""Vale Cadastro Facial / NFC - aplicacao Windows local."""
from __future__ import annotations
import io
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageOps, ImageTk

from core import (GROUPS, TZ, Visitor, load_settings, save_settings, load_api_keys,
                  save_api_keys, parse_local, end_for_ticket, registration_payload,
                  parse_ticket_lines, parse_access_lines, load_pending, save_pending,
                  mark_assigned, clear_pending)
from devices import read_uid, to_card_number
from hikcentral import HikCentral


class ValeApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Vale - Cadastro Facial / NFC")
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
        self.photo_data = b""
        self.preview = None
        self.busy = False
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TButton", padding=7)
        style.configure("TNotebook.Tab", padding=(16, 8))
        self._build()
        self._fill_settings()
        if load_pending():
            self.status.set("Há um visitante pendente; use CONCLUIR PENDENTE antes do próximo.")

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
        self.book = ttk.Notebook(self)
        self.book.pack(fill="both", expand=True, padx=13, pady=10)
        self.registration_tab = ttk.Frame(self.book, padding=12)
        self.settings_tab = ttk.Frame(self.book, padding=12)
        self.book.add(self.registration_tab, text="Cadastro")
        self.book.add(self.settings_tab, text="Configurações")
        self._registration()
        self._settings()
        self.status = tk.StringVar(value="Informe AppKey/AppSecret em Configurações e sincronize.")
        ttk.Label(self, textvariable=self.status).pack(fill="x", padx=18, pady=10)

    def _registration(self):
        actions = ttk.Frame(self.registration_tab)
        actions.pack(fill="x", pady=5)
        ttk.Button(actions, text="Sincronizar grupos e níveis", command=self.connect).pack(side="left")
        ttk.Button(actions, text="Concluir pendente", command=self.resume).pack(side="left", padx=12)

        frame = ttk.LabelFrame(self.registration_tab, text="Visitante", padding=10)
        frame.pack(fill="x", pady=9)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        self.first, self.last, self.group = tk.StringVar(), tk.StringVar(), tk.StringVar(value=GROUPS[0])
        self.reservation = tk.StringVar()
        self._entry(frame, 0, 0, "Nome", self.first)
        self._entry(frame, 0, 1, "Sobrenome", self.last)
        ttk.Label(frame, text="Grupo Visitor").grid(row=1, column=0, sticky="w", padx=4)
        self.groups_box = ttk.Combobox(frame, textvariable=self.group, state="readonly", values=GROUPS)
        self.groups_box.grid(row=1, column=1, sticky="ew", padx=4)
        self._entry(frame, 1, 1, "Reserva (opcional)", self.reservation)

        card = ttk.LabelFrame(self.registration_tab, text="Pulseira NFC e foto", padding=10)
        card.pack(fill="x", pady=7)
        card_left = ttk.Frame(card)
        card_left.pack(side="left", fill="both", expand=True, padx=(0, 18))
        self.card = tk.StringVar()
        self.uid = tk.StringVar(value="UID não lido")
        ttk.Label(card_left, text="Número do cartão / pulseira para HikCentral").pack(anchor="w")
        ttk.Entry(card_left, textvariable=self.card, width=35).pack(fill="x", pady=7)
        ttk.Button(card_left, text="Ler ACR122U (PC/SC)", command=self.scan).pack(anchor="w")
        ttk.Label(card_left, textvariable=self.uid).pack(anchor="w", pady=6)
        ttk.Label(card_left, text="Confira conversão HEX/decimal no primeiro teste.").pack(anchor="w")
        self.photo_widget = tk.Label(card, text="SEM FOTO", width=20, height=7, bg="#e5edf3")
        self.photo_widget.pack(side="left", padx=10)
        controls = ttk.Frame(card)
        controls.pack(side="left")
        ttk.Button(controls, text="Escolher foto", command=self.browse_photo).pack(fill="x", pady=5)
        ttk.Button(controls, text="Foto webcam", command=self.camera).pack(fill="x", pady=5)

        period = ttk.LabelFrame(self.registration_tab, text="Ingresso e validade", padding=10)
        period.pack(fill="x", pady=7)
        period.columnconfigure(1, weight=1)
        period.columnconfigure(3, weight=1)
        self.ticket, self.start, self.end = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(period, text="Ingresso").grid(row=0, column=0, sticky="w", padx=4)
        self.ticket_box = ttk.Combobox(period, textvariable=self.ticket, state="readonly")
        self.ticket_box.grid(row=0, column=1, sticky="ew", padx=4)
        self.ticket_box.bind("<<ComboboxSelected>>", lambda _: self.calculate())
        ttk.Button(period, text="Calcular saída", command=self.calculate).grid(row=0, column=2, columnspan=2, sticky="e")
        self._entry(period, 1, 0, "Entrada", self.start)
        self._entry(period, 1, 1, "Saída", self.end)
        ttk.Label(period, text="DD/MM/AAAA HH:MM • Saída ajustável manualmente").grid(row=2, column=0, columnspan=4, sticky="w")

        buttons = ttk.Frame(self.registration_tab)
        buttons.pack(fill="x", pady=14)
        ttk.Label(buttons, text="Foto e pulseira obrigatórias. Reserva no campo remark.").pack(side="left")
        ttk.Button(buttons, text="CADASTRAR DIRETAMENTE NO HIKCENTRAL",
                   command=self.register).pack(side="right")

    def _settings(self):
        self.s_host, self.s_port, self.s_ca = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.s_key, self.s_secret = tk.StringVar(), tk.StringVar()
        self.s_format, self.s_camera = tk.StringVar(), tk.StringVar()
        frame = ttk.LabelFrame(self.settings_tab, text="OpenAPI local", padding=10)
        frame.pack(fill="x", pady=8)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)
        self._entry(frame, 0, 0, "IP/DNS", self.s_host)
        self._entry(frame, 0, 1, "Porta OpenAPI", self.s_port)
        self._entry(frame, 1, 0, "CA (PEM opcional)", self.s_ca)
        self._entry(frame, 2, 0, "AppKey", self.s_key)
        self._entry(frame, 2, 1, "AppSecret", self.s_secret, show="*")
        ttk.Label(frame, text="Usuário admin do Web Client NÃO substitui AppKey/AppSecret. TLS permanece verificado.").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=6)
        device = ttk.LabelFrame(self.settings_tab, text="Dispositivos", padding=10)
        device.pack(fill="x", pady=6)
        ttk.Label(device, text="Formato do UID").pack(side="left")
        ttk.Combobox(device, textvariable=self.s_format, state="readonly",
                     values=["DECIMAL_BIG", "DECIMAL_LITTLE", "HEX"], width=18).pack(side="left", padx=8)
        ttk.Label(device, text="Índice da webcam").pack(side="left")
        ttk.Spinbox(device, from_=0, to=9, textvariable=self.s_camera, width=4).pack(side="left", padx=6)

        options = ttk.Frame(self.settings_tab)
        options.pack(fill="both", expand=True, pady=9)
        options.columnconfigure(0, weight=1)
        options.columnconfigure(1, weight=1)
        options.rowconfigure(0, weight=1)
        tickets = ttk.LabelFrame(options, text="Ingressos: Nome;Dias;HH:MM", padding=8)
        tickets.grid(row=0, column=0, sticky="nsew", padx=6)
        self.tickets_text = tk.Text(tickets, height=8, width=26, font=("Consolas", 10))
        self.tickets_text.pack(fill="both", expand=True)
        access = ttk.LabelFrame(options, text="Níveis: Grupo=Nivel1,Nivel2", padding=8)
        access.grid(row=0, column=1, sticky="nsew", padx=6)
        self.access_text = tk.Text(access, height=8, width=44, font=("Consolas", 9), wrap="word")
        self.access_text.pack(fill="both", expand=True)
        ttk.Button(self.settings_tab, text="Salvar", command=self.save_config).pack(side="right", pady=5)
        ttk.Button(self.settings_tab, text="Testar conexão", command=self.connect).pack(side="right", padx=9)

    def _fill_settings(self):
        s = self.settings
        for var, key in [(self.s_host, "host"), (self.s_port, "port"), (self.s_ca, "ca_file"),
                         (self.s_format, "card_format"), (self.s_camera, "camera_index")]:
            var.set(str(s[key]))
        self.s_key.set(self.key)
        self.s_secret.set(self.secret)
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
        try:
            result = end_for_ticket(parse_local(self.start.get()), self.settings["tickets"][self.ticket.get()])
            self.end.set(result.strftime("%d/%m/%Y %H:%M"))
        except (KeyError, ValueError) as exc:
            self.status.set(str(exc))

    def save_config(self):
        try:
            conf = {
                "host": self.s_host.get().strip(),
                "port": int(self.s_port.get()),
                "ca_file": self.s_ca.get().strip(),
                "card_format": self.s_format.get(),
                "camera_index": int(self.s_camera.get()),
                "tickets": parse_ticket_lines(self.tickets_text.get("1.0", tk.END)),
                "group_access": parse_access_lines(self.access_text.get("1.0", tk.END))
            }
            if not conf["host"] or not 1 <= conf["port"] <= 65535:
                raise ValueError("Host/porta inválidos.")
            if conf["ca_file"] and not Path(conf["ca_file"]).is_file():
                raise ValueError("Arquivo CA inválido.")
            key, secret = self.s_key.get().strip(), self.s_secret.get().strip()
            if not key or not secret:
                raise ValueError("Configure a AppKey e AppSecret (OpenAPI).")
            save_api_keys(key, secret)
            save_settings(conf)
            self.settings, self.key, self.secret = conf, key, secret
            self.verified = False
            self.ticket_box["values"] = list(conf["tickets"])
            if self.ticket.get() not in conf["tickets"]:
                self.ticket.set(next(iter(conf["tickets"])))
            self.calculate()
            messagebox.showinfo("Configuração", "Salvo. Sincronize novamente antes do cadastro.")
        except Exception as exc:
            messagebox.showerror("Configuração", str(exc))

    def client(self):
        s = self.settings
        return HikCentral(s["host"], s["port"], self.key, self.secret, s["ca_file"])

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

    def connect(self):
        def work():
            client = self.client()
            return client.visitor_groups(), client.access_levels()
        def finish(value, error):
            if error:
                self.verified = False
                self.status.set(str(error))
                messagebox.showerror("Conexão", str(error))
                return
            groups, levels = value
            if not groups:
                self.verified = False
                messagebox.showerror("Conexão", "API retornou zero grupos Visitor.")
                return
            self.connected_groups = groups
            self.groups_box["values"] = groups
            if self.group.get() not in groups:
                self.group.set(groups[0])
            self.verified = True
            self.status.set(f"Conectado: {len(groups)} grupos Visitor e {len(levels)} níveis.")
            self.book.select(self.registration_tab)
        self.task(work, finish)

    def use_photo(self, source):
        try:
            img = ImageOps.exif_transpose(Image.open(source)).convert("RGB")
            img.thumbnail((900, 900))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=86, optimize=True)
            self.photo_data = out.getvalue()
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
            try:
                for _ in range(8):
                    ok, frame = cam.read()
                if not ok:
                    raise RuntimeError("Webcam sem imagem.")
                ok, jpg = cv2.imencode(".jpg", frame)
                if not ok:
                    raise RuntimeError("Erro JPEG.")
            finally:
                cam.release()
            self.use_photo(io.BytesIO(jpg.tobytes()))
        except Exception as exc:
            messagebox.showerror("Webcam", str(exc))

    def scan(self):
        def work():
            reader, uid = read_uid()
            return reader, uid, to_card_number(uid, self.settings["card_format"])
        def finish(value, error):
            if error:
                messagebox.showerror("Leitor NFC", str(error))
                return
            reader, uid, number = value
            self.card.set(number)
            self.uid.set(f"UID HEX: {uid.hex().upper()}  |  {reader[:32]}")
            self.status.set("Pulseira lida; confirme número no HikCentral.")
        self.task(work, finish)

    def register(self):
        if load_pending():
            messagebox.showwarning("Pendente", "Conclua o visitante anterior primeiro.")
            return
        if not self.verified:
            messagebox.showwarning("Verificação", "Sincronize os grupos antes de cadastrar.")
            return
        try:
            group = self.group.get()
            if group not in self.connected_groups:
                raise ValueError("Grupo não confirmado pela API.")
            if group not in self.settings["group_access"]:
                raise ValueError("Configure níveis de acesso para este grupo.")
            obj = Visitor(self.first.get(), self.last.get(), group, self.photo_data,
                          self.card.get().strip(), parse_local(self.start.get()),
                          parse_local(self.end.get()), self.reservation.get())
            payload = registration_payload(obj)
            levels = self.settings["group_access"][group]
        except Exception as exc:
            messagebox.showerror("Dados", str(exc))
            return

        def work():
            client = self.client()
            ids = client.resolve_access_ids(levels)
            visitor_id = client.create_visitor(payload)
            # Só criar uma vez: registrar ID antes de atribuir níveis.
            save_pending(visitor_id, ids)
            for level_id in ids:
                client.assign_access(visitor_id, level_id)
                mark_assigned(level_id)
            client.apply()
            clear_pending()
            return visitor_id
        def finish(visitor_id, error):
            if error:
                pending = load_pending()
                if pending:
                    messagebox.showwarning("Cadastro parcial",
                        f"Visitante ID {pending['visitor_id']} criado.\n{error}\n"
                        "Use CONCLUIR PENDENTE, não refaça o cadastro.")
                else:
                    messagebox.showerror("Resultado incerto",
                        f"{error}\nConfira se a pessoa já existe no HikCentral antes de tentar novamente.")
                self.status.set("Operação não concluída.")
                return
            messagebox.showinfo("Cadastro enviado",
                f"Visitor ID: {visitor_id}\nPermissões reaplicadas. Verifique distribuição nos terminais.")
            self.status.set(f"Check-in criado no HikCentral: {visitor_id}")
            for var in (self.first, self.last, self.card, self.reservation):
                var.set("")
            self.uid.set("UID não lido")
            self.photo_data, self.preview = b"", None
            self.photo_widget.configure(image="", text="SEM FOTO", width=20, height=7)
        self.task(work, finish)

    def resume(self):
        if not load_pending():
            messagebox.showinfo("Pendências", "Nenhum visitante pendente.")
            return
        def work():
            client = self.client()
            data = load_pending()
            for level in data["access_ids"]:
                if level not in data["assigned"]:
                    client.assign_access(data["visitor_id"], level)
                    mark_assigned(level)
            client.apply()
            clear_pending()
            return data["visitor_id"]
        def finish(value, error):
            if error:
                messagebox.showerror("Pendente", str(error))
                return
            self.status.set(f"Reaplicação solicitada para {value}.")
            messagebox.showinfo("Pendente", "Concluído. Confira distribuição nos dispositivos.")
        self.task(work, finish)


if __name__ == "__main__":
    ValeApp().mainloop()
