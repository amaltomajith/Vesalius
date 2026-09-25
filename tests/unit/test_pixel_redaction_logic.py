"""Deterministic tests for S4's confidence-threshold policy in apply_burned_in_redaction,
with common.ocr.detect_text_regions mocked out to inject canned confidence values -- real
OCR confidence output is inherently variable, so the threshold *logic* (redact vs. ignore
vs. flag-for-review) is tested here in isolation, pure and infra-free. Genuine end-to-end
OCR behavior is covered separately by tests/integration/test_burned_in_redaction.py (@ocr).
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
from pydicom.dataset import Dataset

from pipeline.common.errors import LowConfidenceBurnedInTextError
from pipeline.common.ocr import TextRegion
from pipeline.stages.s4_deidentify.pixel_redaction import apply_burned_in_redaction


def _dataset_with_mocked_pixel_array(monkeypatch) -> Dataset:
    ds = Dataset()
    arr = np.zeros((20, 20), dtype=np.int16)
    monkeypatch.setattr(Dataset, "pixel_array", property(lambda self: arr), raising=False)
    return ds


def test_high_confidence_region_is_redacted(monkeypatch) -> None:
    ds = _dataset_with_mocked_pixel_array(monkeypatch)
    with patch(
        "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions",
        return_value=[TextRegion(bbox=(2, 2, 10, 8), text="PHI", confidence=90.0)],
    ):
        records, needs_review = apply_burned_in_redaction(
            ds, high_confidence=70.0, low_confidence=40.0
        )

    assert needs_review is False
    assert any(r.pixel_region == [2, 2, 10, 8] for r in records)
    assert ds.BurnedInAnnotation == "NO"


def test_ambiguous_confidence_region_flags_for_review_and_mutates_nothing(monkeypatch) -> None:
    ds = _dataset_with_mocked_pixel_array(monkeypatch)
    original_pixel_data = getattr(ds, "PixelData", None)
    with patch(
        "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions",
        return_value=[TextRegion(bbox=(2, 2, 10, 8), text="maybe", confidence=55.0)],
    ):
        records, needs_review = apply_burned_in_redaction(
            ds, high_confidence=70.0, low_confidence=40.0
        )

    assert needs_review is True
    assert records == []
    assert getattr(ds, "PixelData", None) == original_pixel_data  # untouched
    # Not set either -- caller must quarantine, not silently pass the instance through.
    assert "BurnedInAnnotation" not in ds


def test_low_confidence_region_is_treated_as_noise_and_ignored(monkeypatch) -> None:
    ds = _dataset_with_mocked_pixel_array(monkeypatch)
    with patch(
        "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions",
        return_value=[
            TextRegion(bbox=(2, 2, 10, 8), text="noise", confidence=10.0),  # below low_confidence
            TextRegion(bbox=(12, 2, 18, 8), text="PHI", confidence=90.0),  # above high_confidence
        ],
    ):
        records, needs_review = apply_burned_in_redaction(
            ds, high_confidence=70.0, low_confidence=40.0
        )

    # The noise region contributes to neither the redact set nor the ambiguous set --
    # only the genuine high-confidence region gets redacted, and the run doesn't need review.
    assert needs_review is False
    assert len(records) == 1
    assert records[0].pixel_region == [12, 2, 18, 8]


def test_no_regions_found_still_records_an_audit_row(monkeypatch) -> None:
    ds = _dataset_with_mocked_pixel_array(monkeypatch)
    with patch(
        "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions", return_value=[]
    ):
        records, needs_review = apply_burned_in_redaction(
            ds, high_confidence=70.0, low_confidence=40.0
        )

    assert needs_review is False
    assert len(records) == 1
    assert records[0].tag_name == "__no_text_found__"
    assert ds.BurnedInAnnotation == "NO"


def test_low_confidence_error_carries_the_documented_reason_code() -> None:
    """apply_burned_in_redaction only signals needs_review=True -- the actual
    LowConfidenceBurnedInTextError is raised by the caller (s4_deidentify.service),
    matching common/errors.py's QuarantineError-subclass-per-reason-code pattern.
    """
    exc = LowConfidenceBurnedInTextError({"reason": "test"})
    assert exc.reason_code == "S4_BURNED_IN_TEXT_LOW_CONFIDENCE"
