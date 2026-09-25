"""The `segmentation_report` artifact -- written for every series a preset is run against,
mirroring S7's report discipline: sorted keys, no timestamps, no run id, plain floats, so a
rebuild of the same inputs is byte-identical.
"""

from __future__ import annotations

import json
import math
from typing import Any
from uuid import UUID

import numpy as np

from pipeline.stages.s8_segment import params

REPORT_VERSION = "1"

RESIDUAL_RISKS: tuple[str, ...] = (
    "thresholds are the GEOMETRY.md presets, unvalidated on real anatomy (noise, beam "
    "hardening, metal, table, partial volume all shift where a threshold actually falls)",
    "opening erodes plates thinner than its iteration count in voxels -- thin cortical bone "
    "near 1mm spacing can be removed along with speckle",
    "closing/opening treat outside the volume's true edge as background: a structure flush "
    "against that edge can be eroded there by up to (closing + opening) iterations; this is a "
    "documented, deliberately un-compensated scipy border artifact, not a threshold defect",
    "largest-component selection keeps exactly one connected piece: a whole-body bone mask "
    "with disconnected bones loses every piece but the largest",
    "coverage > 90% is the only automated signal that a threshold is wrong; a threshold that "
    "is wrong in a way that still passes both sanity gates is not caught",
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
    deface_block: dict[str, Any],
    volume_ref: dict[str, str],
    meta_ref: dict[str, str],
    mask_ref: dict[str, str],
    report_key: str,
    stats: dict[str, Any],
    header_check: dict[str, Any],
) -> bytes:
    body: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "series_id": str(series_id),
        "algorithm": params.algorithm_params(preset),
        "deface": deface_block,
        "input": {"volume": volume_ref, "meta": meta_ref},
        "output": {"mask": mask_ref, "report_object_key": report_key},
        "stats": stats,
        "header_check": header_check,
        "residual_risks": list(RESIDUAL_RISKS),
    }
    return json.dumps(_plain(body), sort_keys=True, indent=2).encode("utf-8")
