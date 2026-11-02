"""Tela de captura facial guiada. Somente pré-validação offline da imagem.

Webcam/detector rodam em thread dedicada; Tk fica apenas no thread principal.
Nenhum frame é enviado, salvo em disco ou usado para identificar pessoas.
"""
from __future__ import annotations

import io
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageDraw, ImageTk
import customtkinter as ctk

from face_capture_logic import (CaptureGate, FaceGuideConfig, FaceAssessment,
                                LocalFaceDetector)


class GuidedFaceCamera:
    def __init__(self, parent, camera_index, on_accept, settings):
        self.parent = parent
        self.index = int(camera_index)
        self.on_accept = on_accept
        self.config = FaceGuideConfig.from_settings(settings)
        self.gate = CaptureGate(self.config)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.seq = 0
        self.shown_seq = -1
        self.captured = None
        self.closed = False
        self.started = time.monotonic()
        self.error = ""
        self.detector_error = ""
        self.after_id = None
        self.image = None

        self.window = ctk.CTkToplevel(parent)
        self.window.title("Captura facial inteligente — Vale")
        self.window.geometry("790x660")
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.configure(fg_color="#F3F6FB")
        self.window.lift()
        self.window.focus_force()
        self._build()
        self.worker = threading.Thread(target=self._capture_worker,
                                       daemon=True, name="ValeFaceCamera")
        self.worker.start()
        self.after_id = self.window.after(75, self._refresh)

    def _build(self):
        title = ctk.CTkLabel(self.window, text="Posicione seu rosto na moldura",
                            font=("Segoe UI", 23, "bold"), text_color="#182841")
        title.pack(pady=(15, 8))
        ctk.CTkLabel(self.window, text="A captura acontece sozinha quando a imagem estiver adequada.",
                     font=("Segoe UI", 12), text_color="#687A95").pack()
        frame = ctk.CTkFrame(self.window, fg_color="#FFFFFF", corner_radius=14)
        frame.pack(fill="both", expand=True, padx=17, pady=12)
        self.preview = tk.Label(frame, text="Inicializando câmera...", bg="#17212b",
                                fg="white", width=90, height=26,
                                font=("Segoe UI", 15))
        self.preview.pack(padx=8, pady=8)
        self.prompt = tk.StringVar(value="Aguardando imagem da câmera...")
        self.quality = tk.StringVar(value="Avaliando enquadramento e iluminação")
        ctk.CTkLabel(self.window, textvariable=self.prompt,
                     font=("Segoe UI", 19, "bold"), text_color="#1763D5").pack(pady=(0, 2))
        ctk.CTkLabel(self.window, textvariable=self.quality,
                     font=("Segoe UI", 11), text_color="#687A95").pack(pady=(0, 10))
        actions = ctk.CTkFrame(self.window, fg_color="transparent")
        actions.pack(fill="x", padx=22, pady=(0, 15))
        self.manual = ctk.CTkButton(actions, text="Capturar manualmente",
                                    command=self.capture_manual, width=175, state="disabled")
        self.manual.pack(side="left", padx=(0, 8))
        self.retake_button = ctk.CTkButton(actions, text="Tirar novamente",
                                          command=self.retake, width=145,
                                          state="disabled", fg_color="#64748B")
        self.retake_button.pack(side="left", padx=7)
        self.accept_button = ctk.CTkButton(actions, text="Usar esta foto",
                                          command=self.accept, width=142, state="disabled")
        self.accept_button.pack(side="left", padx=7)
        ctk.CTkButton(actions, text="Cancelar", command=self.close,
                      fg_color="#64748B", width=92).pack(side="right")

    def _capture_worker(self):
        camera = None
        try:
            import cv2
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            camera = cv2.VideoCapture(self.index, backend)
            if not camera.isOpened():
                raise RuntimeError("Webcam indisponível. Verifique a conexão e o índice.")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            try:
                detector = LocalFaceDetector() if self.config.enabled else None
            except Exception as exc:
                detector = None
                self.detector_error = "Detector facial indisponível. Use Capturar manualmente."
            last_checked = 0.0
            result = FaceAssessment("Posicione o rosto na câmera", "aguardando", False)
            fails = 0
            while not self.stop_event.is_set():
                ok, frame = camera.read()
                if not ok or frame is None:
                    fails += 1
                    if fails > 15:
                        raise RuntimeError("A webcam parou de fornecer imagem.")
                    self.stop_event.wait(.12)
                    continue
                fails = 0
                now = time.monotonic()
                if detector and now - last_checked >= .14:
                    try:
                        result = detector.analyze(frame, self.config)
                    except Exception:
                        detector = None
                        self.detector_error = "Falha na detecção automática. Use Capturar manualmente."
                    last_checked = now
                with self.lock:
                    # Only one current frame in memory, no unbounded image queue.
                    self.latest = (frame, result, now)
                    self.seq += 1
                self.stop_event.wait(.055)
        except Exception as exc:
            self.error = str(exc)
        finally:
            if camera is not None:
                camera.release()

    def _refresh(self):
        if self.closed or not self.window.winfo_exists():
            return
        with self.lock:
            current = self.latest
            seq = self.seq
        if self.captured is None and current is not None and seq != self.shown_seq:
            self.shown_seq = seq
            frame, assessed, captured_at = current
            self.manual.configure(state="normal")
            now = time.monotonic()
            if now - captured_at > .7:
                self.gate.reset()
                self.prompt.set("Aguardando imagem da câmera...")
            elif self.config.enabled and not self.detector_error:
                message, countdown, do_capture = self.gate.step(assessed, now)
                self.prompt.set(str(countdown) if countdown else message)
                self.quality.set(
                    f"Nitidez {assessed.blur:.0f}  •  Luz {assessed.brightness:.0f}  "
                    f"•  Rosto {assessed.ratio * 100:.1f}%")
                if do_capture:
                    self._freeze(frame)
            else:
                self.prompt.set("Ajuste o rosto e utilize captura manual")
            if self.captured is None:
                self._paint(frame, assessed.bbox, self.gate.state == "contagem")
        if self.captured is None:
            if self.error:
                self.prompt.set(self.error)
                self.quality.set("Webcam indisponível: selecione uma foto no cadastro.")
                self.manual.configure(state="disabled")
            elif self.detector_error:
                self.quality.set(self.detector_error)
            elif self.config.enabled and time.monotonic() - self.started > self.config.timeout_s:
                self.quality.set("Tempo de orientação excedido. Pode capturar manualmente.")
        self.after_id = self.window.after(75, self._refresh)

    def _paint(self, frame, bbox=None, counting=False):
        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((740, 440), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image)
        w, h = image.size
        draw.ellipse((int(w*.16), int(h*.04), int(w*.84), int(h*.96)),
                     outline="#41D6B0" if counting else "#F3C24F", width=5)
        if bbox:
            fw, fh = frame.shape[1], frame.shape[0]
            x, y, bw, bh = bbox
            draw.rectangle((int(x*w/fw), int(y*h/fh), int((x+bw)*w/fw),
                            int((y+bh)*h/fh)), outline="#41D6B0" if counting else "#57A5FF", width=3)
        self.image = ImageTk.PhotoImage(image)
        self.preview.configure(image=self.image, text="", width=w, height=h)

    def _freeze(self, frame):
        if self.closed or self.captured is not None:
            return
        self.captured = frame.copy()
        self.prompt.set("Foto capturada com sucesso!")
        self.quality.set("Confira a imagem ou tire novamente.")
        self.manual.configure(state="disabled")
        self.retake_button.configure(state="normal")
        self.accept_button.configure(state="normal")
        self._paint(self.captured)

    def capture_manual(self):
        with self.lock:
            current = self.latest
        if current is None:
            messagebox.showwarning("Webcam", "Aguarde uma imagem da câmera.", parent=self.window)
            return
        self._freeze(current[0])

    def retake(self):
        self.captured = None
        self.gate.reset()
        self.started = time.monotonic()
        self.retake_button.configure(state="disabled")
        self.accept_button.configure(state="disabled")
        self.prompt.set("Posicione o rosto na câmera")
        self.quality.set("Nova captura iniciada")
        self.manual.configure(state="normal")

    def accept(self):
        if self.captured is None:
            return
        import cv2
        ok, jpg = cv2.imencode(".jpg", self.captured, [cv2.IMWRITE_JPEG_QUALITY, 94])
        if not ok:
            messagebox.showerror("Webcam", "Não foi possível preparar o JPEG.", parent=self.window)
            return
        try:
            # Original app.use_photo processes JPEG and limits size to 2 MB.
            accepted = self.on_accept(io.BytesIO(jpg.tobytes()))
        except Exception as exc:
            messagebox.showerror("Webcam", str(exc), parent=self.window)
            return
        if accepted is not False:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        if self.after_id is not None:
            try:
                self.window.after_cancel(self.after_id)
            except tk.TclError:
                pass
        self.captured = None
        with self.lock:
            self.latest = None
        if self.window.winfo_exists():
            self.window.destroy()
        if getattr(self.parent, "_active_face_window", None) is self:
            self.parent._active_face_window = None


def open_face_capture(app):
    instance = getattr(app, "_active_face_window", None)
    if instance is not None and not instance.closed:
        instance.window.lift()
        return
    capture = GuidedFaceCamera(app, app.settings["camera_index"],
                               app.use_photo, app.settings)
    app._active_face_window = capture
