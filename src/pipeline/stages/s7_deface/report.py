"""The `deface_report` artifact -- evidence for every series, defaced or not.

Written for EVERY series so the absence of defacing is always evidenced (a non-head series gets
a report that says why nothing was done). Deterministic: sorted keys, no timestamps, no run id,
plain floats, so a rebuild of the same inputs is byte-identical. It doubles as the input to S9's
later before/after visual.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any
from uuid import UUID

import numpy as np

from pipeline.stages.s7_deface import params
from pipeline.stages.s7_deface.decision import DefaceDecision

REPORT_VERSION = "1"

RESIDUAL_RISKS: tuple[str, ...] = (
    "the body-part label is trusted: a head mislabelled as a non-head body part is not defaced",
    "efficacy is validated only on a synthetic phantom, not on real anatomy or against a face "
    "recognition tool",
    "the independent verifier shares the algorithm's anatomical assumptions",
    "the facial skeleton and teeth remain and can identify a forensic-reconstruction adversary",
    "bone below the bone floor (thin orbital roof, cribriform plate) lets the strip leak up to the "
    "strip depth",
    "anterior orbit and nasal/oral cavity contents are stripped",
    "faces with more than the strip depth of soft tissue leave a remnant",
    "ears lie outside the anterior slab; a strongly chin-up head has an oblique face plane",
    "silver DICOM instances and the silver S6 volume are not defaced; they are protected only by "
    "never being served",
)


def _plain(value: Any) -> Any:
    """JSON-safe, numpy-free, deterministic (non-finite floats become strings)."""
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


def packed_mask_sha256(mask: np.ndarray) -> str:
    return hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()


def build_report(
    *,
    series_id: UUID,
    decision: DefaceDecision,
    volume_ref: dict[str, str],
    meta_ref: dict[str, str],
    defaced_ref: dict[str, str] | None,
    report_key: str,
    detail: dict[str, Any] | None,
) -> bytes:
    """`detail` is None when defacing was not required; otherwise the algorithm/verifier
    outputs (stats, region, change, efficacy, header check)."""
    body: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "series_id": str(series_id),
        "decision": {
            **decision.to_block(),
            "vocab_version": decision.vocab_version,
            "evidence": decision.evidence,
        },
        "applied": detail is not None,
        "input": {"volume": volume_ref, "meta": meta_ref},
        "output": {"volume_defaced": defaced_ref, "report_object_key": report_key},
        "algorithm": params.algorithm_params() if detail is not None else None,
        "residual_risks": list(RESIDUAL_RISKS),
    }
    if detail is not None:
        body.update(detail)
    return json.dumps(_plain(body), sort_keys=True, indent=2).encode("utf-8")
