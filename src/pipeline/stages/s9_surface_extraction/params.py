"""Versioned parameters of S9's marching-cubes extraction. Everything here changes S9's output,
so all of it is folded into `transform_runs.parameters` (never inlined elsewhere).

Every numeric threshold is an UNVALIDATED PLACEHOLDER, same caveat class as S7/S8's -- tuned
against the synthetic head phantom, not real anatomy.
"""

from __future__ import annotations

from pipeline.common.hu_presets import PRESETS_V1, PRESETS_VERSION

STAGE = "S9"
STAGE_VERSION = "1.0.0"
PARAMS_VERSION = "1"

DEFAULT_PRESET = "bone"  # matches S8's own default, so the common path needs no flags

# GEOMETRY.md's own example value; face-count control is S10's job (quadric decimation), not
# S9's ("Marching cubes on a full CT commonly yields 2-5 million faces, so decimation is not
# optional" -- GEOMETRY.md).
STEP_SIZE = 1

# Voxels the S8 mask is dilated by before it confines marching cubes (see mesh_extract.py's
# docstring for why: it gives the isosurface real neighbouring intensity to interpolate against
# instead of an abrupt substituted background everywhere the mask boundary doesn't already
# coincide with a natural threshold crossing).
DILATION_VOXELS = 2

# A world-space vertex must lie within the volume's own bounding box plus this much slack
# (catches a wrong-axis or un-converted vertex array cheaply; not a real physical margin).
WORLD_BBOX_TOLERANCE_MM = 5.0

# The S8 mask's own recorded affine (parsed from its NIfTI header) must agree with the gated
# volume's affine within this tolerance -- same tolerance class as S6/S7/S8's header checks.
AFFINE_TOLERANCE_MM = 1e-3

# PLY's binary vertex encoding is float32 (verified empirically -- see mesh_io.py), so a write
# -> re-read round trip is not bit-exact even when correct. Generous relative to float32
# precision (~1e-7), tiny relative to any real corruption.
VERTEX_ROUND_TRIP_TOLERANCE_MM = 1e-3


def level_for(preset: str) -> float:
    """The preset's own min threshold -- GEOMETRY.md: `skin_surface`'s min "is the preset that
    produces a face"; generalises directly to every preset as its natural isosurface value."""
    return float(PRESETS_V1[preset]["min"])


def algorithm_params(preset: str) -> dict[str, object]:
    return {
        "preset": preset,
        "preset_version": PRESETS_VERSION,
        "level": level_for(preset),
        "step_size": STEP_SIZE,
        "dilation_voxels": DILATION_VOXELS,
        "world_bbox_tolerance_mm": WORLD_BBOX_TOLERANCE_MM,
    }
