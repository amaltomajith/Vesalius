"""A small deterministic feature table -- the consumer's "workload".

Thresholds are this module's own (not the pipeline's segmentation presets: the consumer must not
import them). Everything spatial goes through the RAS affine, so results describe the physical
object and do not depend on how the array happens to be oriented.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from consumer.loader import Volume

BODY_HU = -500.0
# disjoint HU bins, [low, high); the gaps (-500..-150, 300 boundary) are partial-volume / unlabelled
CLASSES: dict[str, tuple[float, float]] = {
    "air": (-np.inf, -900.0),
    "lung_like": (-900.0, -500.0),
    "fat": (-150.0, -30.0),
    "soft_tissue": (-30.0, 300.0),
    "bone": (300.0, np.inf),
}
DECIMALS = 3


def _r(x: float) -> float:
    return round(float(x), DECIMALS)


def compute_features(volume: Volume) -> dict[str, Any]:
    a = volume.array
    sx, sy, sz = volume.spacing_mm
    voxel_ml = sx * sy * sz / 1000.0
    body = a > BODY_HU

    n_body = int(body.sum())
    features: dict[str, Any] = {
        "shape_zyx": list(a.shape),
        "spacing_mm_xyz": [_r(sx), _r(sy), _r(sz)],
        "voxel_volume_ml": _r(voxel_ml),
        "body_volume_ml": _r(n_body * voxel_ml),
        "class_volume_ml": {
            name: _r(int(((a >= lo) & (a < hi)).sum()) * voxel_ml)
            for name, (lo, hi) in CLASSES.items()
        },
    }
    if n_body == 0:
        features.update(
            mean_hu_body=None, std_hu_body=None, centroid_ras_mm=None, extent_ras_mm=None
        )
        return features

    body_vals = a[body].astype(np.float64)
    features["mean_hu_body"] = _r(body_vals.mean())
    features["std_hu_body"] = _r(body_vals.std())

    # centroid and extent in RAS mm via the affine, accumulated slice by slice (bounded memory)
    m = volume.affine_ras
    total = np.zeros(3)
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for k in range(a.shape[0]):
        jj, ii = np.nonzero(body[k])
        if jj.size == 0:
            continue
        idx = np.stack([ii, jj, np.full(ii.shape, k)]).astype(np.float64)  # (3, n): i, j, k
        ras = m[:3, :3] @ idx + m[:3, 3:4]
        total += ras.sum(axis=1)
        lo = np.minimum(lo, ras.min(axis=1))
        hi = np.maximum(hi, ras.max(axis=1))
    features["centroid_ras_mm"] = [_r(v) for v in total / n_body]
    features["extent_ras_mm"] = {
        "min": [_r(v) for v in lo],
        "max": [_r(v) for v in hi],
    }
    return features
