"""Offline Chinese OCR over a captured window bitmap.

RapidOCR (ONNX) runs fully locally - the image never leaves the machine and
there is no cloud OCR account involved. Loaded lazily because the model takes a
moment to initialise.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np

_engine = None
_engine_lock = threading.Lock()


@dataclass
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int
    score: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
    return _engine


def warm() -> None:
    """Load the model up front so the first real message is not slow."""
    try:
        _get_engine()
    except Exception:
        pass


def run_ocr(img: np.ndarray) -> list[OcrLine]:
    """Return recognised text lines with their boxes in image coordinates."""
    engine = _get_engine()
    # RapidOCR accepts a BGR/RGB ndarray; boxes come back as 4 corner points.
    result, _elapse = engine(img)
    lines: list[OcrLine] = []
    if not result:
        return lines
    for box, text, score in result:
        if not text:
            continue
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        x, y = int(min(xs)), int(min(ys))
        w, h = int(max(xs) - min(xs)), int(max(ys) - min(ys))
        lines.append(OcrLine(str(text).strip(), x, y, w, h, float(score)))
    lines.sort(key=lambda ln: ln.y)
    return lines
