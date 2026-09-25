"""Versioned parameters that affect S6's output -- folded into every S6 transform_run's
idempotency key so a future change forces re-assembly rather than silently reusing a
stale volume (DICOM.md: "Record the target spacing as a versioned parameter -- it changes
output geometry, so it belongs in transform_runs").

RESAMPLE_TARGET_SPACING_MM_V1 is an unvalidated placeholder pending real fixture tuning
(DICOM.md gives no number, only "record... as a versioned parameter") -- same caveat class
as S4's OCR confidence thresholds. 1.0mm is applied uniformly across every series rather
than adapted per-series, matching DICOM.md's phrasing of one governed value.
"""

from __future__ import annotations

RESAMPLE_TARGET_SPACING_MM_V1: float = 1.0
PARAMS_VERSION: str = "1"

# STAGES.md S6 "Fails on: ... fewer than 3 slices" -- also TESTING.md's QG_TOO_FEW_SLICES
# threshold, re-verified independently here (see service.py's cross-check design).
MIN_SLICES = 3

# Same tolerance s3_quality_gate/checks.py uses for QG_ORIENTATION_MIXED (TESTING.md:
# "Any variation > 1e-4"), re-verified independently at S6 against the actual silver bytes.
ORIENTATION_TOLERANCE = 1e-4

# Cross-check tolerance (§3): how far the freshly-recomputed slice_spacing/spacing_uniform
# may drift from S3's persisted series.slice_spacing/spacing_uniform before S6 treats it as
# a real disagreement (S6_GEOMETRY_DRIFT) rather than floating-point noise. Deliberately
# tight -- KEEP_TAGS guarantees the underlying tag values are byte-identical between what
# S3 saw and what S6 reads, so any drift beyond float roundoff is a real invariant
# violation, not an expected source of variance.
SPACING_DRIFT_TOLERANCE_MM = 1e-6
