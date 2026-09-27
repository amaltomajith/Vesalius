"""The `mesh_postprocess_report` artifact -- written for every series a preset is run against,
mirroring S7-S9's report discipline: sorted keys, no timestamps, no run id, plain floats, so a
rebuild of the same inputs is byte-identical.
"""

from __future__ import annotations

import json
import math
from typing import Any
from uuid import UUID

import numpy as np

from pipeline.stages.s10_mesh_postprocess import params

REPORT_VERSION = "1"

RESIDUAL_RISKS: tuple[str, ...] = (
    "Taubin and decimation parameters are unvalidated placeholders on real anatomy "
    "(GEOMETRY.md's own example values, with the Taubin nu sign corrected -- see "
    "mesh_postprocess.py)",
    "fast-simplification has no boundary- or normal-preservation option (pymeshlab's "
    "preserveboundary/preservenormal); open boundaries of a mesh may move during decimation",
    "largest-component selection at the mesh level can still discard a real disconnected "
    "anatomical structure -- the same class of risk as S8's voxel-level version",
    "decimation determinism was verified on this project's phantom/synthetic scale, not at "
    "multi-million-face real-CT scale",
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
    target_faces: int,
    deface_block: dict[str, Any],
    input_ref: dict[str, str],
    output_ref: dict[str, str],
    report_key: str,
    stats: dict[str, Any],
) -> bytes:
    body: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "series_id": str(series_id),
        "algorithm": params.algorithm_params(target_faces),
        "deface": deface_block,
        "input": {"mesh": input_ref},
        "output": {"mesh": output_ref, "report_object_key": report_key},
        "stats": stats,
        "residual_risks": list(RESIDUAL_RISKS),
    }
    return json.dumps(_plain(body), sort_keys=True, indent=2).encode("utf-8")
