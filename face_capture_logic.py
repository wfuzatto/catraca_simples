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
    """Exact offline OpenCV YuNet model/threshold already used by face_scanner.

    The model is SHA256-pinned during build and embedded by PyInstaller.
    Never silently downgrade to Haar: a fallback would recreate the false
    'no face' result observed at the front desk.
    """
    MODEL_NAME = "face_detection_yunet_2023mar.onnx"

    def __init__(self, model_path=None, detection_threshold=0.85):
        import cv2
        import sys
        self.cv2 = cv2
        if not hasattr(cv2, "FaceDetectorYN"):
            raise RuntimeError("OpenCV não suporta YuNet. Use captura manual.")
        model = Path(model_path) if model_path is not None else (
            Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
            / "models" / self.MODEL_NAME
        )
        if not model.is_file():
            raise RuntimeError("Modelo YuNet ausente do aplicativo. Capture manualmente.")
        self.detector = cv2.FaceDetectorYN.create(
            str(model), "", (320, 320), float(detection_threshold), 0.3, 5000
        )
        self.engine = "yunet"
        self.model_path = str(model)

    def analyze(self, frame, config):
        cv2 = self.cv2
        h, w = frame.shape[:2]
        # At reception, preserve a fast offline pipeline on CPU-only machines.
        scale = min(1.0, 640 / max(1, w))
        small = (cv2.resize(frame, (max(1, round(w * scale)), max(1, round(h * scale))))
                 if scale < 1 else frame)
        sh, sw = small.shape[:2]
        self.detector.setInputSize((sw, sh))
        _, detected = self.detector.detect(small)
        count = 0 if detected is None else len(detected)
        if count != 1:
            return assess_face(count, None, (w, h), config=config)
        row = detected[0]
        sx, sy, fw, fh = [float(v) for v in row[:4]]
        # Preserve unclamped bounds for assessment of cropped faces.
        box = (round(sx / scale), round(sy / scale),
               round(fw / scale), round(fh / scale))
        x, y, bw, bh = box
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        gray = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY) if x1 > x0 and y1 > y0 else None
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var()) if gray is not None and gray.size else 0.0
        bright = float(gray.mean()) if gray is not None and gray.size else 0.0
        # YuNet returns five landmarks; use eyes (first two pairs) to assess tilt.
        eye1 = (float(row[4]), float(row[5]))
        eye2 = (float(row[6]), float(row[7]))
        left, right = sorted((eye1, eye2), key=lambda p: p[0])
        angle = degrees(atan2(right[1] - left[1], max(right[0] - left[0], 0.001)))
        return assess_face(1, box, (w, h), blur=blur,
                           brightness=bright, angle=angle, config=config)
