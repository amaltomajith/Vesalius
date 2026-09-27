"""Shared OCR rendering -- pixel array to image, image to detected text regions. No
policy here (what counts as a redaction target vs. a verification failure is
channel-specific, decided separately by s4_deidentify/pixel_redaction.py and
s5_verify/pixel_verify.py). Sharing this rendering step is fine and necessary (one source
of truth for "how pixels become an image tesseract can read"); the decisions built on top
of it are independently written per stage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np
import pytesseract
from PIL import Image

# On Windows the installer (UB-Mannheim) puts tesseract at a fixed path that may not be
# in PATH in the current shell session.  Set PYTESSERACT_PATH in .env to point directly
# at the binary -- pytesseract will use it and won't try to look up PATH at all.
_tess_path = os.environ.get("PYTESSERACT_PATH") or os.environ.get(
    "TESSERACT_CMD",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe" if os.name == "nt" else "tesseract",
)
if os.path.isfile(_tess_path):
    pytesseract.pytesseract.tesseract_cmd = _tess_path


@dataclass(frozen=True)
class TextRegion:
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1
    text: str
    confidence: float  # 0-100, pytesseract's scale (-1 for non-text blocks, filtered out)


def render_pixel_array_to_image(pixel_array: np.ndarray) -> Image.Image:
    """Normalizes an arbitrary-bit-depth grayscale pixel array to 8-bit for OCR. Handles
    already-8-bit arrays (no-op scaling) and higher-bit-depth arrays (CT/MR, typically
    12-16 bit) by scaling the array's own observed min/max into [0, 255] -- a fixed
    HU-style window is not assumed, since this must work across modalities.
    """
    arr = pixel_array
    if arr.ndim == 3:  # multi-sample (e.g. RGB secondary capture) -- collapse to luminance
        arr = arr.mean(axis=-1)
    arr = arr.astype(np.float64)
    lo, hi = float(arr.min()), float(arr.max())
    if hi <= lo:
        normalized = np.zeros_like(arr, dtype=np.uint8)
    else:
        normalized = ((arr - lo) / (hi - lo) * 255.0).astype(np.uint8)
    return Image.fromarray(normalized, mode="L")


def detect_text_regions(image: Image.Image) -> list[TextRegion]:
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    regions: list[TextRegion] = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        conf = float(data["conf"][i])
        if not text or conf < 0:  # tesseract uses -1 for non-text block markers
            continue
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        regions.append(TextRegion(bbox=(x, y, x + w, y + h), text=text, confidence=conf))
    return regions
