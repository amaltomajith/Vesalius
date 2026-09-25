"""The `surface_extraction_report` artifact -- written for every series a preset is run
against, mirroring S7/S8's report discipline: sorted keys, no timestamps, no run id, plain
floats, so a rebuild of the same inputs is byte-identical.
"""

from __future__ import annotations

import json
import math
from typing import Any
from uuid import UUID

import numpy as np

from pipeline.stages.s9_surface_extraction import params

REPORT_VERSION = "1"

RESIDUAL_RISKS: tuple[str, ...] = (
    "the mask confines but marching cubes still interpolates from raw intensity -- wherever "
    "S8's morphological cleanup moved the mask boundary away from the raw threshold crossing, "
    "the surface there reflects the substituted background more than a real anatomical edge",
    "mesh quality is unvalidated on real anatomy; the phantom shows shape and "
    "orientation-independence, not clinical fidelity",
    "a mislabelled body part upstream still produces a face mesh -- S9 trusts the defacing "
    "gate's recorded decision the same way S8 does",
    "mask dilation before confinement is an unvalidated placeholder",
    "face count is not controlled here -- decimation happens at S10",
)


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if math.isfinite(f) else str(f)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def build_report(
    *,
    series_id: UUID,
    preset: str,
    skip_deface: bool,
    deface_block: dict[str, Any],
    volume_ref: dict[str, str],
    meta_ref: dict[str, str],
    mask_ref: dict[str, str],
    mesh_ref: dict[str, str],
    report_key: str,
    stats: dict[str, Any],
) -> bytes:
    body: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "series_id": str(series_id),
        "algorithm": params.algorithm_params(preset),
        "skip_deface_requested": skip_deface,  # recorded for audit; never changes control flow
        "deface": deface_block,
        "input": {"volume": volume_ref, "meta": meta_ref, "mask": mask_ref},
        "output": {"mesh": mesh_ref, "report_object_key": report_key},
        "stats": stats,
        "residual_risks": list(RESIDUAL_RISKS),
    }
    return json.dumps(_plain(body), sort_keys=True, indent=2).encode("utf-8")
