"""Versioned parameters of S8's thresholding + morphological cleanup. Everything here changes
S8's output, so all of it is folded into `transform_runs.parameters` (never inlined elsewhere).

Cleanup order and step counts follow GEOMETRY.md's "Morphological cleanup" (closing(2) ->
opening(1) -> largest component), which is more specific than STAGES.md's "closing then largest
component" and is what the user confirmed. Cost, recorded here rather than hidden: opening drops
speckle but also erodes plates thinner than ~3 voxels, which can remove thin cortical bone at
1mm spacing -- a documented residual risk, not a bug.
"""

from __future__ import annotations

from pipeline.common.hu_presets import PRESETS_V1, PRESETS_VERSION

STAGE = "S8"
STAGE_VERSION = "1.0.0"
PARAMS_VERSION = "1"

# GEOMETRY.md's skin_surface note ("must never run on head anatomy without defacing") is enforced
# structurally: S8 obtains volume bytes only through open_defacing_gate (see service.py), never
# by reading the raw S6/S7 volume itself.
SUPPORTED_PRESETS = ("bone", "bone_dense", "soft_tissue", "lung", "skin_surface")
DEFAULT_PRESETS = ("bone",)  # user-confirmed default; others are opt-in via --preset

CLOSING_ITERATIONS = 2
OPENING_ITERATIONS = 1
CONNECTIVITY = 6  # GEOMETRY.md's default scipy.ndimage.label structure (face-connectivity)

MAX_MASK_FRACTION = 0.90  # STAGES.md S8: >90% of the volume means the threshold is wrong


def preset_bounds(preset: str) -> tuple[float, float]:
    if preset not in SUPPORTED_PRESETS:
        raise KeyError(preset)
    p = PRESETS_V1[preset]
    return float(p["min"]), float(p["max"])


def algorithm_params(preset: str) -> dict[str, object]:
    lo, hi = preset_bounds(preset)
    return {
        "preset": preset,
        "preset_version": PRESETS_VERSION,
        "thresholds": {"min": lo, "max": hi},
        "cleanup": {
            "closing_iterations": CLOSING_ITERATIONS,
            "opening_iterations": OPENING_ITERATIONS,
            "connectivity": CONNECTIVITY,
        },
        "max_mask_fraction": MAX_MASK_FRACTION,
    }
