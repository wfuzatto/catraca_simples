"""Synthetic face metrics: no visitors, biometric templates or physical webcam."""
from __future__ import annotations
import io
import numpy as np
import pytest
from PIL import Image
from face_capture_logic import (
    assess_face, CaptureGate, FaceGuideConfig, FaceAssessment, LocalFaceDetector,
)

W,H = 640,480
CENTER = (220,130,200,200)


def good(config=None, bbox=CENTER):
    return assess_face(1,bbox,(W,H),blur=105,brightness=125,angle=2,config=config)


@pytest.mark.parametrize("count,bbox,word", [
    (0,None,"Posicione"), (2,CENTER,"Apenas uma pessoa"),
    (1,(300,200,60,60),"Aproxime"),
    (1,(100,30,440,360),"Afaste"),
    (1,(8,130,200,200),"Centralize")
])
def test_invalid_framing_does_not_capture(count,bbox,word):
    result=assess_face(count,bbox,(W,H),blur=105,brightness=125)
    assert word in result.message
    assert not result.ready
    assert CaptureGate().step(result,now=500)[2] is False


@pytest.mark.parametrize("brightness,blur,angle,expected", [
    (22,100,0,"iluminação"),(235,100,0,"luz forte"),
    (125,20,0,"foco"),(125,100,20,"cabeça reta")
])
def test_image_quality_blocks_automatic_capture(brightness,blur,angle,expected):
    result=assess_face(1,CENTER,(W,H),brightness=brightness,blur=blur,angle=angle)
    assert expected in result.message
    assert not result.ready


def test_face_centered_stable_counts_3_2_1_then_captures():
    gate=CaptureGate(FaceGuideConfig(stable_ms=900))
    frame=good()
    assert frame.ready
    assert gate.step(frame,100.0)==("Perfeito! Mantenha a posição",None,False)
    assert gate.step(frame,100.91)[1]==3
    assert gate.step(frame,101.61)[1]==2
    assert gate.step(frame,102.35)[1]==1
    assert gate.step(frame,102.87)[2] is True
    assert gate.state=="capturado"
    assert gate.step(frame,103)[2] is False


def test_move_during_countdown_resets_timer():
    gate=CaptureGate()
    frame=good()
    gate.step(frame,100)
    gate.step(frame,101)
    moved=good(bbox=(279,130,200,200))
    assert moved.ready
    text,num,capture=gate.step(moved,101.2)
    assert not capture
    assert num is None
    assert gate.countdown_since is None
    assert gate.step(moved,101.3)[2] is False


def test_invalid_light_interrupts_countdown():
    gate=CaptureGate()
    gate.step(good(),10)
    assert gate.step(good(),11)[1]==3
    bad=assess_face(1,CENTER,(W,H),brightness=12,blur=95)
    assert gate.step(bad,11.2)[1] is None
    assert gate.step(good(),11.4)[2] is False


def test_retake_resets_state_and_unlimited_manual_mode():
    cfg=FaceGuideConfig(enabled=False)
    gate=CaptureGate(cfg)
    assert gate.step(good(),100)[2] is False
    assert not cfg.enabled
    gate=CaptureGate()
    gate.step(good(),0)
    gate.step(good(),1)
    gate.step(good(),3)
    assert gate.completed
    gate.reset()
    assert not gate.completed and gate.ready_since is None


def test_quality_off_preserves_framing_checks():
    cfg=FaceGuideConfig(require_quality=False)
    assert assess_face(1,CENTER,(W,H),blur=0,brightness=5,config=cfg).ready
    assert not assess_face(1,(1,130,200,200),(W,H),
                           blur=80,brightness=130,config=cfg).ready


def test_options_default_and_bounds():
    cfg=FaceGuideConfig.from_settings({})
    assert cfg.enabled and cfg.stable_ms == 900 and cfg.require_quality
    for key,value in (("stable_ms",200),("stable_ms",4000),
                      ("sensitivity","Desconhecida"),("timeout_s",3)):
        with pytest.raises(ValueError):
            FaceGuideConfig(**{key:value})


def test_detector_loads_offline_haar_xml_from_opencv_installation():
    detector=LocalFaceDetector()
    assert not detector.face.empty()


def test_capture_is_valid_jpeg_without_server_or_disk():
    import cv2
    from core import prepare_face_jpeg
    frame=np.full((800,1200,3),125,dtype=np.uint8)
    ok,jpeg=cv2.imencode(".jpg",frame,[cv2.IMWRITE_JPEG_QUALITY,94])
    assert ok
    processed=prepare_face_jpeg(jpeg.tobytes())
    with Image.open(io.BytesIO(processed)) as im:
        assert im.format=="JPEG"
        assert max(im.size)<=1024
    assert len(processed)<=2_000_000


def test_gui_import_does_not_start_a_webcam_or_network():
    import face_capture_ui
    assert callable(face_capture_ui.open_face_capture)


def test_no_photo_is_automatically_sent_to_hikcentral():
    import inspect
    import face_capture_ui
    code=inspect.getsource(face_capture_ui.GuidedFaceCamera.accept)
    assert "on_accept" in code
    assert "HikCentral" not in code
