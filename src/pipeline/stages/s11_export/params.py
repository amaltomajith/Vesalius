"""Versioned parameters of S11's export. Everything here changes S11's output, so all of it is
folded into `transform_runs.parameters` (never inlined elsewhere).
"""

from __future__ import annotations

from pipeline.stages.s6_volume_assembly.meta import CONTRACT_NAME, CONTRACT_VERSION
from pipeline.version import PIPELINE_VERSION

STAGE = "S11"
STAGE_VERSION = "1.0.0"
PARAMS_VERSION = "1"

DEFAULT_PRESET = "bone"

# GEOMETRY.md's own example values (200k default) is S10's concern, not re-chosen here.

THUMBNAIL_SIZE_PX = (256, 256)
THUMBNAIL_LOW_PERCENTILE = 1.0
THUMBNAIL_HIGH_PERCENTILE = 99.0

# GLB's binary accessor encoding is float32 (glTF spec), same verified-empirically property as
# S9/S10's PLY writer -- a write -> re-read round trip is not bit-exact even when correct.
VERTEX_ROUND_TRIP_TOLERANCE_MM = 1e-3


def algorithm_params() -> dict[str, object]:
    return {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "thumbnail_size_px": list(THUMBNAIL_SIZE_PX),
        "thumbnail_percentile_range": [THUMBNAIL_LOW_PERCENTILE, THUMBNAIL_HIGH_PERCENTILE],
    }
