"""Hounsfield-unit threshold presets, VERBATIM from refs/GEOMETRY.md ("Threshold presets").

One module so S7 (defacing: which voxels count as body/soft tissue) and S8 (segmentation:
which voxels become the mask) read the same numbers -- if they diverged, S8 could mesh voxels
that S7's head mask never considered. A test compares this table against GEOMETRY.md itself.

Versioned parameters: record `PRESETS_VERSION` in `transform_runs.parameters`, never inline
the literals.
"""

from __future__ import annotations

PRESETS_VERSION = "v1"

PRESETS_V1: dict[str, dict[str, int]] = {
    "bone": {"min": 300, "max": 3000},
    "bone_dense": {"min": 500, "max": 3000},
    "soft_tissue": {"min": -50, "max": 300},
    "lung": {"min": -900, "max": -500},
    "vessel_ce": {"min": 150, "max": 500},  # requires contrast
    # the outer body surface -- the preset that makes a face
    "skin_surface": {"min": -200, "max": 3000},
}
