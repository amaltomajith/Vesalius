"""Shared DICOM geometry math -- DICOM.md Part 2's slice-position projection and spacing
derivation. Single source of truth so the pipeline's most error-prone algorithm
(STAGES.md S6: "the stage most likely to be silently wrong") is implemented exactly once.

S3 is the first caller (quality-gate spacing/gap checks); S6 (volume assembly, sorting and
HU derivation) is the second, and should read S3's persisted `instances.slice_position` /
`series.slice_spacing` rather than recomputing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# TESTING.md Part 1: "Deviation > 1% of median" (QG_SPACING_NONUNIFORM) and
# "Max delta vs median > 2x median spacing" (QG_SLICE_GAP). Exported for checks.py to
# compare SpacingResult's ratios against, so the threshold lives in exactly one place.
NONUNIFORM_DEVIATION_RATIO = 0.01
SLICE_GAP_RATIO = 2.0


class MissingGeometryError(ValueError):
    """Orientation/position values absent or malformed -- caller decides how to route this
    (S3: QG_MISSING_GEOMETRY; S6: hard failure per STAGES.md).
    """


def slice_position_from_values(
    orientation: Sequence[float] | None, position: Sequence[float] | None
) -> float:
    """DICOM.md Part 2's slice_position(), operating on already-extracted tag values
    (what S3 has after extract_tags()) rather than requiring a live pydicom Dataset.
    """
    if orientation is None or position is None or len(orientation) != 6 or len(position) != 3:
        raise MissingGeometryError("ImageOrientationPatient/ImagePositionPatient missing")
    iop = np.array(orientation, dtype=float)
    row_cosine, col_cosine = iop[:3], iop[3:]
    normal = np.cross(row_cosine, col_cosine)
    ipp = np.array(position, dtype=float)
    return float(np.dot(ipp, normal))


@dataclass(frozen=True)
class SpacingResult:
    slice_spacing: float | None  # median(diff(sorted positions)); None if < 2 positions
    spacing_uniform: bool | None  # None if < 2 positions
    max_deviation_ratio: float | None  # max_i |diff_i - median| / |median|; None if spacing == 0
    max_gap_ratio: float | None  # max(diff) / |median|; None if spacing == 0
    sorted_positions: list[float]


def derive_slice_spacing(positions: Sequence[float]) -> SpacingResult:
    """DICOM.md Part 2's spacing derivation. Sorts by slice_position itself -- callers
    must NOT pre-sort by InstanceNumber/SliceLocation (STAGES.md's trap #1).
    """
    sorted_positions = sorted(positions)
    if len(sorted_positions) < 2:
        return SpacingResult(None, None, None, None, sorted_positions)

    diffs = np.diff(np.array(sorted_positions, dtype=float))
    spacing_z = float(np.median(diffs))

    if spacing_z == 0.0:
        # QG_SPACING_ZERO is its own reason code, distinct from "nonuniform" -- deviation
        # ratios are undefined (division by zero) when the median itself is zero, so this
        # case is reported without a ratio rather than attempting one.
        return SpacingResult(0.0, False, None, None, sorted_positions)

    deviation_ratio = float(np.max(np.abs(diffs - spacing_z)) / abs(spacing_z))
    gap_ratio = float(np.max(diffs) / abs(spacing_z))
    uniform = deviation_ratio <= NONUNIFORM_DEVIATION_RATIO
    return SpacingResult(spacing_z, uniform, deviation_ratio, gap_ratio, sorted_positions)
