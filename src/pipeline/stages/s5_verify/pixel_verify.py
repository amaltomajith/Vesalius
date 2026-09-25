"""S5's independent OCR verification -- a separate call into common/ocr.py's shared
rendering (data/plumbing, not policy) with its own, stricter threshold and identifier-shape
pattern matching, not just "any text present". Deliberately does not import
s4_deidentify/pixel_redaction.py.
"""

from __future__ import annotations

import re

import pydicom

from pipeline.common.ocr import detect_text_regions, render_pixel_array_to_image
from pipeline.stages.s5_verify.models import VerifyFailure

OCR_VERIFY_PROFILE_VERSION = "1"
# Stricter than S4's redaction threshold (70) -- S5's job is to catch anything S4 missed,
# and any hit here quarantines rather than continues, so a lower floor (more false
# positives tolerated) is the safer failure mode.
VERIFY_CONFIDENCE_THRESHOLD = 55.0

# Identifier-shape patterns: digit runs typical of an MRN/phone/accession number, and
# Name^Case-style DICOM person-name tokens -- not just "any text present", since some
# vendor overlays (window/level readouts, orientation markers "L"/"R") are legitimate and
# not PHI. Placeholder patterns pending real tuning against actual fixtures, same caveat as
# S4's confidence thresholds -- DICOM.md gives no spec for this, only qualitative guidance.
_IDENTIFIER_PATTERNS = (
    re.compile(r"\d{5,}"),  # long digit runs (MRN/phone/accession-like)
    re.compile(r"[A-Za-z]+\^[A-Za-z]+"),  # DICOM person-name shape: Last^First
)


def check_pixel_ocr_clean(
    ds: pydicom.dataset.Dataset, *, threshold: float = VERIFY_CONFIDENCE_THRESHOLD
) -> VerifyFailure | None:
    pixel_array = ds.pixel_array
    image = render_pixel_array_to_image(pixel_array)
    regions = detect_text_regions(image)

    offenders = [
        {"bbox": list(r.bbox), "confidence": r.confidence}
        for r in regions
        if r.confidence >= threshold and any(p.search(r.text) for p in _IDENTIFIER_PATTERNS)
    ]
    if offenders:
        return VerifyFailure("S5_BURNED_IN_TEXT_DETECTED", {"offenders": offenders})
    return None
