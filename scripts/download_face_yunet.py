"""Download the same pinned YuNet model already used by wfuzatto/face_scanner.

Build-time only. No downloads or server requests at the hotel workstation.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
from urllib.request import Request, urlopen

NAME = "face_detection_yunet_2023mar.onnx"
COMMIT = "47534e27c9851bb1128ccc0102f1145e27f23f98"
SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
SIZE = 232589
URLS = [
    f"https://media.githubusercontent.com/media/opencv/opencv_zoo/{COMMIT}/models/face_detection_yunet/{NAME}",
    f"https://github.com/opencv/opencv_zoo/raw/{COMMIT}/models/face_detection_yunet/{NAME}",
]

def valid(data):
    return len(data) == SIZE and hashlib.sha256(data).hexdigest() == SHA256

def main():
    path = Path(__file__).resolve().parents[1] / "models" / NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and valid(path.read_bytes()):
        print("YuNet verificado:", path)
        return
    errors = []
    for url in URLS:
        try:
            with urlopen(Request(url, headers={"User-Agent":"ValeCadastroFacialNFC/1.0"}), timeout=45) as r:
                data = r.read(SIZE+4096)
            if not valid(data):
                raise ValueError("YuNet retornou dados inválidos, LFS pointer, ou SHA/size incorretos")
            temp = path.with_suffix(".download")
            temp.write_bytes(data)
            temp.replace(path)
            print("YuNet SHA-256 verificado:", SHA256)
            return
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Não foi possível obter YuNet validado: " + "; ".join(errors))

if __name__ == "__main__":
    main()
