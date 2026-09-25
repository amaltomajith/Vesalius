"""S4 channel 3 -- burned-in pixel text detection and redaction. DICOM.md:
"BurnedInAnnotation... is frequently absent or wrong -- do not trust it... OCR the
rendered pixel array, find text bounding boxes, redact by filling with the background
value... When OCR confidence is low, flag for review rather than auto-redacting --
over-redaction destroys anatomy and is not recoverable."

Thresholds are placeholder numbers pending real tuning -- DICOM.md gives no numeric spec,
only qualitative guidance. Treat OCR_REDACTION_PROFILE_VERSION as the thing to bump if
these are retuned.
"""

from __future__ import annotations

import numpy as np
import pydicom

from pipeline.common.ocr import TextRegion, detect_text_regions, render_pixel_array_to_image
from pipeline.stages.s4_deidentify.models import DeidActionRecord

OCR_REDACTION_PROFILE_VERSION = "1"
HIGH_CONFIDENCE_THRESHOLD = 70.0  # >= this: safe to auto-redact
LOW_CONFIDENCE_THRESHOLD = 40.0  # < this: treated as noise, ignored entirely
# Between the two: ambiguous -- DICOM.md's "flag for review", not auto-redacted, not ignored.


def _clip_bbox(
    bbox: tuple[int, int, int, int], shape: tuple[int, int]
) -> tuple[int, int, int, int]:
    height, width = shape
    x0, y0, x1, y1 = bbox
    return (max(0, x0), max(0, y0), min(width, x1), min(height, y1))


def _estimate_background(pixel_array: np.ndarray, regions: list[TextRegion]) -> float:
    plane = pixel_array.mean(axis=-1) if pixel_array.ndim == 3 else pixel_array
    mask = np.ones(plane.shape, dtype=bool)
    for region in regions:
        x0, y0, x1, y1 = _clip_bbox(region.bbox, plane.shape)
        mask[y0:y1, x0:x1] = False
    values = plane[mask]
    if values.size == 0:
        return float(plane.flat[0])
    return float(np.median(values))


def apply_burned_in_redaction(
    ds: pydicom.dataset.Dataset,
    *,
    high_confidence: float = HIGH_CONFIDENCE_THRESHOLD,
    low_confidence: float = LOW_CONFIDENCE_THRESHOLD,
) -> tuple[list[DeidActionRecord], bool]:
    """Returns (audit_records, needs_review). needs_review=True means the caller must
    quarantine the instance (LowConfidenceBurnedInTextError) rather than redact or pass
    it through -- nothing in `ds` is mutated in that case.
    """
    pixel_array = ds.pixel_array
    image = render_pixel_array_to_image(pixel_array)
    regions = detect_text_regions(image)

    redact_regions = [r for r in regions if r.confidence >= high_confidence]
    ambiguous = [r for r in regions if low_confidence <= r.confidence < high_confidence]

    if ambiguous:
        return [], True

    records: list[DeidActionRecord] = []
    if redact_regions:
        background = _estimate_background(pixel_array, redact_regions)
        arr = pixel_array.copy()
        for region in redact_regions:
            x0, y0, x1, y1 = _clip_bbox(region.bbox, arr.shape[:2])
            arr[y0:y1, x0:x1] = background
            records.append(
                DeidActionRecord(
                    channel="pixel",
                    action="redact",
                    rule_id=f"ocr-redaction-v{OCR_REDACTION_PROFILE_VERSION}",
                    pixel_region=[x0, y0, x1, y1],
                )
            )
        ds.PixelData = arr.astype(pixel_array.dtype).tobytes()

    # Set honestly, not blindly -- DICOM.md: "Redact, then set the tag honestly." An audit
    # row is recorded even when zero regions were found, proving the scan ran rather than
    # merely that the tag was overwritten unconditionally.
    ds.BurnedInAnnotation = "NO"
    if not records:
        records.append(
            DeidActionRecord(
                channel="pixel",
                action="redact",
                rule_id=f"ocr-redaction-v{OCR_REDACTION_PROFILE_VERSION}",
                tag_name="__no_text_found__",
            )
        )
    return records, False
