"""Captura facial local: guia adaptado do wfuzatto/face_scanner.

Apenas detecção de presença, posição e qualidade; não identifica indivíduos.
O fluxo de cadastro do HikCentral permanece exclusivamente no app.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil, degrees, atan2
from pathlib import Path
import time


@dataclass(frozen=True)
class FaceGuideConfig:
    enabled: bool = True
    stable_ms: int = 900
    sensitivity: str = "Normal"
    require_quality: bool = True
    timeout_s: int = 35

    def __post_init__(self):
        if not 400 <= self.stable_ms <= 3000:
            raise ValueError("Estabilidade deve ficar entre 400 e 3000 ms.")
        if self.sensitivity not in ("Suave", "Normal", "Rigorosa"):
            raise ValueError("Sensibilidade facial inválida.")
        if not 10 <= self.timeout_s <= 120:
            raise ValueError("Tempo da captura deve ficar entre 10 e 120 segundos.")

    @classmethod
    def from_settings(cls, settings):
        return cls(
            enabled=bool(settings.get("face_auto_capture", True)),
            stable_ms=int(settings.get("face_stability_ms", 900)),
            sensitivity=settings.get("face_sensitivity", "Normal"),
            require_quality=bool(settings.get("face_require_quality", True)),
            timeout_s=int(settings.get("face_timeout_s", 35)),
        )


@dataclass(frozen=True)
class FaceAssessment:
    message: str
    state: str
    ready: bool
    bbox: tuple[int, int, int, int] | None = None
    blur: float = 0.0
    brightness: float = 0.0
    ratio: float = 0.0


def assess_face(face_count, bbox, image_size, *, blur=0.0, brightness=0.0,
                angle=None, config=None):
    """Deterministic decision policy adapted from face_scanner capture-guide.js.

    Brightness 35..225, Laplacian variance 45, face ratio 0.105..0.48,
    centroid tolerance .16/.20 are the proven scanner baseline values.
    Sensitivity adjusts tolerances rather than changing detector identity.
    """
    config = config or FaceGuideConfig()
    if face_count < 1:
        return FaceAssessment("Posicione o rosto na câmera", "aguardando", False)
    if face_count > 1:
        return FaceAssessment("Apenas uma pessoa por vez", "enquadrando", False)
    if bbox is None or image_size[0] <= 0 or image_size[1] <= 0:
        return FaceAssessment("Posicione o rosto na câmera", "aguardando", False)
    x, y, fw, fh = bbox
    w, h = image_size
    if fw <= 0 or fh <= 0:
        return FaceAssessment("Posicione o rosto na câmera", "aguardando", False)
    ratio = (fw * fh) / float(w * h)
    sensitivity = {"Suave": 1.3, "Normal": 1.0, "Rigorosa": 0.8}[config.sensitivity]
    base = dict(bbox=tuple(map(int, bbox)), blur=float(blur),
                brightness=float(brightness), ratio=ratio)
    def reject(message):
        return FaceAssessment(message, "enquadrando", False, **base)
    if ratio < 0.105 / sensitivity:
        return reject("Aproxime o rosto")
    if ratio > 0.48 * sensitivity:
        return reject("Afaste um pouco")
    cx, cy = (x + fw / 2) / w, (y + fh / 2) / h
    if (x < 0.04*w or y < 0.04*h or x+fw > .96*w or y+fh > .96*h
        or abs(cx - .5) > .16*sensitivity or abs(cy-.48) > .20*sensitivity):
        return reject("Centralize o rosto")
    if angle is not None and abs(angle) > 12*sensitivity:
        return reject("Mantenha a cabeça reta")
    if config.require_quality:
        if brightness < 35:
            return reject("Melhore a iluminação")
        if brightness > 225:
            return reject("Evite luz forte no rosto")
        if blur < 45:
            return reject("Aguarde, ajustando o foco")
    return FaceAssessment("Perfeito! Mantenha a posição", "validando", True, **base)


class CaptureGate:
    """Only fires after continuously acceptable/stable face and countdown."""
    def __init__(self, config=None):
        self.config = config or FaceGuideConfig()
        self.reset()

    def reset(self):
        self.ready_since = None
        self.countdown_since = None
        self.last_box = None
        self.completed = False
        self.state = "aguardando"

    @staticmethod
    def _stable(a, b):
        if a is None or b is None:
            return False
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        center_motion = abs(ax + aw/2 - bx - bw/2)/max(aw, bw, 1)
        vertical_motion = abs(ay + ah/2 - by - bh/2)/max(ah, bh, 1)
        scale_change = max(abs(aw-bw)/max(aw,bw,1), abs(ah-bh)/max(ah,bh,1))
        return max(center_motion, vertical_motion) < .12 and scale_change < .15

    def step(self, assessment, now=None):
        """Return (message, countdown 3/2/1 or None, should_capture)."""
        now = time.monotonic() if now is None else now
        if self.completed:
            return ("Foto capturada!", None, False)
        if not self.config.enabled or not assessment.ready:
            self.reset()
            self.state = assessment.state
            return (assessment.message, None, False)
        if self.last_box is not None and not self._stable(assessment.bbox, self.last_box):
            self.ready_since = now
            self.countdown_since = None
        if self.ready_since is None:
            self.ready_since = now
        self.last_box = assessment.bbox
        if now - self.ready_since < self.config.stable_ms/1000:
            self.state = "validando"
            return ("Perfeito! Mantenha a posição", None, False)
        if self.countdown_since is None:
            self.countdown_since = now
        elapsed = now - self.countdown_since
        if elapsed < 1.95:
            self.state = "contagem"
            return ("Perfeito! Não se mexa...", max(1, 3 - int(elapsed/.65)), False)
        self.completed = True
        self.state = "capturado"
        return ("Foto capturada!", None, True)


class LocalFaceDetector:
    """Offline OpenCV Haar cascades bundled by PyInstaller --collect-data cv2."""
    def __init__(self):
        import cv2
        self.cv2 = cv2
        base = Path(cv2.data.haarcascades)
        face = base / "haarcascade_frontalface_default.xml"
        eyes = base / "haarcascade_eye.xml"
        self.face = cv2.CascadeClassifier(str(face))
        self.eyes = cv2.CascadeClassifier(str(eyes))
        if self.face.empty():
            raise RuntimeError("Detector facial não disponível. Use captura manual.")

    def analyze(self, frame, config):
        cv2 = self.cv2
        h, w = frame.shape[:2]
        ratio = min(1.0, 640/max(w, 1))
        small = cv2.resize(frame, (round(w*ratio), round(h*ratio))) if ratio < 1 else frame
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        boxes = self.face.detectMultiScale(gray, scaleFactor=1.09, minNeighbors=5, minSize=(45, 45))
        count = len(boxes)
        if count != 1:
            return assess_face(count, None, (w, h), config=config)
        x,y,fw,fh = [int(v) for v in boxes[0]]
        x0,y0,bw,bh = [int(round(n/ratio)) for n in (x,y,fw,fh)]
        area = gray[y:y+fh, x:x+fw]
        blur = cv2.Laplacian(area, cv2.CV_64F).var() if area.size else 0.0
        bright = float(area.mean()) if area.size else 0.0
        angle = None
        if not self.eyes.empty() and area.size:
            eyes = self.eyes.detectMultiScale(area[:max(1,fh*2//3)], minNeighbors=5, minSize=(10,10))
            if len(eyes) >= 2:
                eye_pair = sorted(eyes, key=lambda e:e[2]*e[3], reverse=True)[:2]
                eye_pair.sort(key=lambda e:e[0])
                ex1,ey1,ew1,eh1 = eye_pair[0]
                ex2,ey2,ew2,eh2 = eye_pair[1]
                angle = degrees(atan2((ey2+eh2/2)-(ey1+eh1/2), (ex2+ew2/2)-(ex1+ew1/2)))
        return assess_face(1, (x0,y0,bw,bh), (w,h), blur=float(blur),
                           brightness=bright, angle=angle, config=config)
