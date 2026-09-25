"""S3's quality-gate checks. Thresholds pulled verbatim from refs/TESTING.md Part 1.

Every check runs (S3 is not fail-fast): a series with multiple simultaneous defects gets
one Quarantine row per failing reason code, each carrying that check's own specific failing
values, rather than a merged/ambiguous blob or only the first failure found.

QG_DUPLICATE_CONTENT is intentionally not implemented here. S1 dedups by content_hash
before anything reaches indexing (s1_ingest/service.py's _prior_success_exists),
instances.content_hash is DB-UNIQUE, and S2's idempotency key is keyed on that same
content_hash -- there is no code path by which a series' instances could contain the same
content twice by the time S3 evaluates them. Implementing this check would be dead code
that can never fire.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pipeline.catalog.models import Series, Study
from pipeline.common.body_part import BodyPartClass, classify_body_part
from pipeline.common.dicom_geometry import (
    NONUNIFORM_DEVIATION_RATIO,
    SLICE_GAP_RATIO,
    SpacingResult,
)
from pipeline.stages.s3_quality_gate.geometry import InstanceGeometry

VOLUMETRIC_MODALITIES = frozenset({"CT", "MR"})

# TESTING.md: "Any variation > 1e-4" for QG_ORIENTATION_MIXED.
_ORIENTATION_TOLERANCE = 1e-4

# TESTING.md: "< 3" for QG_TOO_FEW_SLICES.
_MIN_SLICES = 3


@dataclass(frozen=True)
class GateFailure:
    reason_code: str
    detail: dict[str, object]


def check_too_few_slices(series: Series, geoms: list[InstanceGeometry]) -> GateFailure | None:
    if series.modality not in VOLUMETRIC_MODALITIES:
        return None
    if len(geoms) < _MIN_SLICES:
        return GateFailure(
            "QG_TOO_FEW_SLICES", {"instance_count": len(geoms), "modality": series.modality}
        )
    return None


def check_modality_unsupported(series: Series) -> GateFailure | None:
    if series.modality not in VOLUMETRIC_MODALITIES:
        return GateFailure("QG_MODALITY_UNSUPPORTED", {"modality": series.modality})
    return None


def check_missing_geometry(geoms: list[InstanceGeometry]) -> GateFailure | None:
    offenders = []
    for g in geoms:
        missing = [
            t
            for t in ("ImagePositionPatient", "ImageOrientationPatient", "PixelSpacing")
            if g.tags.get(t) is None
        ]
        if missing:
            offenders.append({"instance_id": str(g.instance_id), "missing_tags": missing})
    if offenders:
        return GateFailure("QG_MISSING_GEOMETRY", {"instances": offenders})
    return None


def check_orientation_mixed(series: Series, geoms: list[InstanceGeometry]) -> GateFailure | None:
    if series.modality not in VOLUMETRIC_MODALITIES:
        return None
    present = [g for g in geoms if g.tags.get("ImageOrientationPatient") is not None]
    if len(present) < 2:
        return None
    ref = np.array(present[0].tags["ImageOrientationPatient"], dtype=float)
    offenders = []
    for g in present[1:]:
        delta = float(np.max(np.abs(np.array(g.tags["ImageOrientationPatient"]) - ref)))
        if delta > _ORIENTATION_TOLERANCE:
            offenders.append({"instance_id": str(g.instance_id), "max_deviation": delta})
    if offenders:
        return GateFailure(
            "QG_ORIENTATION_MIXED",
            {"reference": present[0].tags["ImageOrientationPatient"], "instances": offenders},
        )
    return None


def check_dimension_mismatch(series: Series, geoms: list[InstanceGeometry]) -> GateFailure | None:
    if series.modality not in VOLUMETRIC_MODALITIES or not geoms:
        return None
    ref_rows, ref_cols = geoms[0].tags["Rows"], geoms[0].tags["Columns"]
    offenders = [
        {"instance_id": str(g.instance_id), "rows": g.tags["Rows"], "columns": g.tags["Columns"]}
        for g in geoms[1:]
        if (g.tags["Rows"], g.tags["Columns"]) != (ref_rows, ref_cols)
    ]
    if offenders:
        return GateFailure(
            "QG_DIMENSION_MISMATCH",
            {"reference": {"rows": ref_rows, "columns": ref_cols}, "instances": offenders},
        )
    return None


def check_missing_rescale(series: Series, geoms: list[InstanceGeometry]) -> GateFailure | None:
    if series.modality != "CT":
        return None
    offenders = []
    for g in geoms:
        missing = [t for t in ("RescaleSlope", "RescaleIntercept") if g.tags.get(t) is None]
        if missing:
            offenders.append({"instance_id": str(g.instance_id), "missing_tags": missing})
    if offenders:
        return GateFailure("QG_MISSING_RESCALE", {"instances": offenders})
    return None


def check_pixel_decode(geoms: list[InstanceGeometry]) -> GateFailure | None:
    offenders = [
        {"instance_id": str(g.instance_id), "error": g.pixel_decode_error}
        for g in geoms
        if g.pixel_decode_error is not None
    ]
    if offenders:
        return GateFailure("QG_PIXEL_DECODE_FAILED", {"instances": offenders})
    return None


def check_spacing_zero(series: Series, spacing: SpacingResult) -> GateFailure | None:
    if series.modality not in VOLUMETRIC_MODALITIES or spacing.slice_spacing is None:
        return None
    if spacing.slice_spacing == 0.0:
        return GateFailure("QG_SPACING_ZERO", {"positions": spacing.sorted_positions})
    return None


def check_spacing_nonuniform(series: Series, spacing: SpacingResult) -> GateFailure | None:
    if series.modality not in VOLUMETRIC_MODALITIES or spacing.max_deviation_ratio is None:
        return None
    if spacing.max_deviation_ratio > NONUNIFORM_DEVIATION_RATIO:
        return GateFailure(
            "QG_SPACING_NONUNIFORM",
            {
                "median_spacing": spacing.slice_spacing,
                "max_deviation_ratio": spacing.max_deviation_ratio,
            },
        )
    return None


def check_slice_gap(series: Series, spacing: SpacingResult) -> GateFailure | None:
    if series.modality not in VOLUMETRIC_MODALITIES or spacing.max_gap_ratio is None:
        return None
    if spacing.max_gap_ratio > SLICE_GAP_RATIO:
        return GateFailure(
            "QG_SLICE_GAP",
            {"median_spacing": spacing.slice_spacing, "max_gap_ratio": spacing.max_gap_ratio},
        )
    return None


def check_bodypart_unresolved(study: Study) -> GateFailure | None:
    """TESTING.md: "Not in known vocabulary after inference" -- a hard failure, because an
    unknown study could be a head and the defacing gate cannot be evaluated without knowing.

    Fires on the stored *source* (absent/unrecognised at S2 and not rescued by inference) AND
    on the stored *value's class*, so rows written before the closed vocabulary existed
    (source "tag", value e.g. "XYZ") are caught too. Only a positively-recognised value passes.
    """
    cls = classify_body_part(study.body_part)
    resolved = cls in (BodyPartClass.HEAD_ADJACENT, BodyPartClass.KNOWN_NON_HEAD)
    if study.body_part_source == "unresolved" or study.body_part is None or not resolved:
        return GateFailure(
            "QG_BODYPART_UNRESOLVED",
            {
                "body_part_source": study.body_part_source,
                "body_part_class": cls.value,
                # BodyPartExamined is a CS defined term already held in a public column,
                # not free text; truncated regardless.
                "body_part": (study.body_part or "")[:64] or None,
            },
        )
    return None


def run_all_checks(
    series: Series, geoms: list[InstanceGeometry], spacing: SpacingResult
) -> list[GateFailure]:
    checks = (
        check_too_few_slices(series, geoms),
        check_modality_unsupported(series),
        check_missing_geometry(geoms),
        check_orientation_mixed(series, geoms),
        check_dimension_mismatch(series, geoms),
        check_missing_rescale(series, geoms),
        check_pixel_decode(geoms),
        check_spacing_zero(series, spacing),
        check_spacing_nonuniform(series, spacing),
        check_slice_gap(series, spacing),
        check_bodypart_unresolved(series.study),
    )
    return [c for c in checks if c is not None]
