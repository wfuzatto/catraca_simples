"""Regressões do botão Abrir câmera na edição (sem webcam nem HikCentral)."""
from types import SimpleNamespace
from unittest.mock import Mock
import io

import pytest

from visitor_editor import open_editor_camera


class FakeDialog:
    def __init__(self):
        self.events = []
    def grab_release(self):
        self.events.append("release")
    def grab_set(self):
        self.events.append("restore")
    def winfo_exists(self):
        return True
    def lift(self):
        self.events.append("lift")
    def focus_force(self):
        self.events.append("focus")


class FakeWindow:
    def __init__(self):
        self.events = []
        self.handlers = {}
    def bind(self, name, handler, add=None):
        self.handlers[name] = handler
        assert name == "<Destroy>" and add == "+"
    def wait_visibility(self):
        self.events.append("visible")
    def grab_set(self):
        self.events.append("grab")
    def focus_force(self):
        self.events.append("focus")


def test_editor_uses_same_guided_camera_without_touching_registration(monkeypatch):
    import face_capture_ui
    recorded = {}
    class Camera:
        closed = False
        def __init__(self, parent, index, on_accept, settings):
            recorded.update(parent=parent, index=index, callback=on_accept,
                            settings=settings)
            self.window = FakeWindow()
    monkeypatch.setattr(face_capture_ui, "GuidedFaceCamera", Camera)
    dialog = FakeDialog()
    settings = {"camera_index": 2, "face_auto_capture": True}
    app_registration_photo = b"foto-do-novo-cadastro"
    chosen = []
    camera = open_editor_camera(dialog, settings, lambda source: chosen.append(source.read()))
    assert camera.window.events == ["visible", "grab", "focus"]
    assert dialog.events == ["release"]
    assert recorded["parent"] is dialog and recorded["index"] == 2
    assert recorded["settings"] is settings
    assert chosen == []
    recorded["callback"](io.BytesIO(b"jpeg-da-edicao"))
    assert chosen == [b"jpeg-da-edicao"]
    assert app_registration_photo == b"foto-do-novo-cadastro"
    camera.window.handlers["<Destroy>"](SimpleNamespace(widget=camera.window))
    assert dialog.events == ["release", "restore", "lift", "focus"]


def test_editor_restores_modal_grab_if_camera_fails(monkeypatch):
    import face_capture_ui
    def unavailable(*args):
        raise RuntimeError("Webcam indisponível")
    monkeypatch.setattr(face_capture_ui, "GuidedFaceCamera", unavailable)
    dialog = FakeDialog()
    with pytest.raises(RuntimeError, match="Webcam indisponível"):
        open_editor_camera(dialog, {"camera_index": 0}, Mock())
    assert dialog.events == ["release", "restore"]
