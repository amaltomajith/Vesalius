"""Independent efficacy verifier for S7 -- S5's principle applied to defacing: a defacer that
verifies itself proves nothing.

Deliberately shares NOTHING with `algorithm.py` / `geometry.py` (a static test enforces it):
its own world-coordinate code, its own body mask (air connected to the volume border, 6-connected,
no morphological opening -- the algorithm uses opening + 26-connected largest component + hole
fill), its own depth transform and a wider check zone. It runs on the PERSISTED bytes (the caller
passes the arrays re-read from the written NIfTI), so it also covers the write path.

Hard invariants (a violation means a bug, wrong axis or corrupted write):
  * every changed voxel lies inside the (superset) check zone -- independent of the algorithm's
    own region, so a mirrored / wrong-axis / drifted region is caught here;
  * no voxel >= bone floor changed;
  * every changed voxel equals the removal value;
  * every changed voxel was within strip depth (+ tolerance) of the pre-defacing skin.
Efficacy (fail closed): the front of the head retreated by >= MIN_ANTERIOR_RETREAT_MM and
>= MIN_SKIN_DESTROYED_FRACTION of the skin-surface voxels in the front of the head are gone.

LIMIT: it shares the planner's anatomical assumptions (the face is at the front of the head, ~30 mm
of soft tissue). It catches implementation bugs, not conceptual ones. On real anatomy a failure
means a false quarantine, never a fail-open. Thresholds are phantom-tuned placeholders.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from pipeline.stages.s7_deface import params

_SLAB = 16


@dataclass
class VerifyReport:
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, float | int] = field(default_factory=dict)


def _world_axis(
    affine: np.ndarray, axis: int, shape: tuple[int, int, int], k0: int, k1: int
) -> np.ndarray:
    """RAS coordinate `axis` (0=x right, 1=y anterior, 2=z superior) of slices k0..k1-1.
    Uses the affine in its native (i=column, j=row, k=slice) order."""
    _, nj, ni = shape
    kk: np.ndarray = np.arange(k0, k1, dtype=np.float64).reshape(-1, 1, 1)
    jj: np.ndarray = np.arange(nj, dtype=np.float64).reshape(1, -1, 1)
    ii: np.ndarray = np.arange(ni, dtype=np.float64).reshape(1, 1, -1)
    return (
        float(affine[axis, 0]) * ii
        + float(affine[axis, 1]) * jj
        + float(affine[axis, 2]) * kk
        + float(affine[axis, 3])
    )


def _body(volume: np.ndarray, tissue_min: float) -> np.ndarray:
    """Everything that is not air connected to the volume border, then the largest 6-connected
    piece of it."""
    air = volume < tissue_min
    lab, n = ndimage.label(air)
    if n == 0:
        return np.ones(volume.shape, dtype=bool)
    faces = np.concatenate(
        [
            lab[0].ravel(),
            lab[-1].ravel(),
            lab[:, 0].ravel(),
            lab[:, -1].ravel(),
            lab[:, :, 0].ravel(),
            lab[:, :, -1].ravel(),
        ]
    )
    outside = np.isin(lab, np.unique(faces[faces > 0]))
    del lab, air
    solid = ~outside
    lab2, n2 = ndimage.label(solid)
    if n2 == 0:
        return np.zeros(volume.shape, dtype=bool)
    sizes = np.bincount(lab2.ravel())
    sizes[0] = 0
    return np.asarray(lab2 == int(sizes.argmax()), dtype=bool)


def _bbox(mask: np.ndarray) -> list[tuple[int, int]] | None:
    if not mask.any():
        return None
    out = []
    for axes in ((1, 2), (0, 2), (0, 1)):
        idx = np.flatnonzero(mask.any(axis=axes))
        out.append((int(idx[0]), int(idx[-1])))
    return out


def verify_defacing(
    before: np.ndarray,
    after: np.ndarray,
    affine_ras: np.ndarray,
    *,
    air_value: float,
    spacing_mm: float = 1.0,
) -> VerifyReport:
    failures: list[str] = []
    metrics: dict[str, float | int] = {}
    if before.shape != after.shape or before.ndim != 3:
        return VerifyReport(False, ["shape_mismatch"], {})
    shape = (int(before.shape[0]), int(before.shape[1]), int(before.shape[2]))
    tissue_min = params.TISSUE_MIN_HU

    changed = before != after
    n_changed = int(changed.sum())
    metrics["changed_voxels"] = n_changed
    if n_changed == 0:
        return VerifyReport(False, ["nothing_changed"], metrics)

    body = _body(before, tissue_min)
    nk = shape[0]

    # --- head extents, own world-coordinate code -------------------------------------------------
    s_max = -math.inf
    for k0 in range(0, nk, _SLAB):
        k1 = min(k0 + _SLAB, nk)
        sel = body[k0:k1]
        if sel.any():
            s_max = max(s_max, float(_world_axis(affine_ras, 2, shape, k0, k1)[sel].max()))
    if not math.isfinite(s_max):
        return VerifyReport(False, ["no_body_found"], metrics)
    s_floor = s_max - params.HEAD_HEIGHT_CAP_MM

    a_hi, a_lo_body = -math.inf, math.inf
    for k0 in range(0, nk, _SLAB):
        k1 = min(k0 + _SLAB, nk)
        sel = body[k0:k1] & (_world_axis(affine_ras, 2, shape, k0, k1) >= s_floor)
        if sel.any():
            a = _world_axis(affine_ras, 1, shape, k0, k1)[sel]
            a_hi, a_lo_body = max(a_hi, float(a.max())), min(a_lo_body, float(a.min()))
    ap = a_hi - a_lo_body
    metrics["ap_extent_mm"] = ap
    zone_a = a_hi - params.VERIFIER_ANTERIOR_FRACTION * ap - params.VERIFIER_ZONE_MARGIN_MM
    face_a = a_hi - params.VERIFIER_FACE_FRACTION * ap
    zone_s = s_floor - params.VERIFIER_ZONE_MARGIN_MM

    # --- hard invariants over the whole volume ---------------------------------------------------
    outside_zone = 0
    for k0 in range(0, nk, _SLAB):
        k1 = min(k0 + _SLAB, nk)
        ch = changed[k0:k1]
        if not ch.any():
            continue
        inside = (_world_axis(affine_ras, 1, shape, k0, k1) >= zone_a) & (
            _world_axis(affine_ras, 2, shape, k0, k1) >= zone_s
        )
        outside_zone += int((ch & ~inside).sum())
    metrics["changed_outside_zone"] = outside_zone
    if outside_zone:
        failures.append("changed_outside_zone")

    bone_changed = int(((before >= params.BONE_FLOOR_HU) & changed).sum())
    metrics["bone_voxels_changed"] = bone_changed
    if bone_changed:
        failures.append("bone_changed")

    wrong_value = int((changed & (after != np.float32(air_value))).sum())
    metrics["changed_not_removal_value"] = wrong_value
    if wrong_value:
        failures.append("changed_voxel_not_removal_value")

    # --- depth + skin, on a crop ------------------------------------------------------------
    margin = (
        int(math.ceil((params.STRIP_DEPTH_MM + params.VERIFIER_DEPTH_TOLERANCE_MM) / spacing_mm))
        + 2
    )
    face_full = _face_mask(affine_ras, shape, face_a, s_floor)
    bb = _bbox(changed | (body & (before >= tissue_min) & face_full))
    if bb is None:  # pragma: no cover - changed is non-empty
        return VerifyReport(False, [*failures, "no_zone"], metrics)
    crop = tuple(
        slice(max(lo - margin, 0), min(hi + margin, n - 1) + 1)
        for (lo, hi), n in zip(bb, shape, strict=True)
    )
    b = body[crop]
    depth = ndimage.distance_transform_edt(b, sampling=spacing_mm)
    ch_c = changed[crop]
    too_deep = int(
        (ch_c & (depth > params.STRIP_DEPTH_MM + params.VERIFIER_DEPTH_TOLERANCE_MM)).sum()
    )
    metrics["changed_deeper_than_strip"] = too_deep
    if too_deep:
        failures.append("changed_deeper_than_strip")

    face = face_full[crop]
    v_c, a_c = before[crop], after[crop]
    skin = (
        b
        & face
        & (depth <= params.VERIFIER_SKIN_DEPTH_MM)
        & (v_c >= tissue_min)
        & (v_c < params.BONE_FLOOR_HU)
    )
    n_skin = int(skin.sum())
    metrics["face_zone_skin_voxels"] = n_skin
    if n_skin == 0:
        failures.append("no_skin_in_face_zone")
        destroyed = 0.0
    else:
        destroyed = float((a_c[skin] < tissue_min).mean())
    metrics["skin_destroyed_fraction"] = destroyed
    if n_skin and destroyed < params.MIN_SKIN_DESTROYED_FRACTION:
        failures.append("skin_not_destroyed")

    # --- anterior retreat: front-most surviving body voxel vs original ------------------------
    after_body = body & (after >= tissue_min)
    front_after = -math.inf
    for k0 in range(0, nk, _SLAB):
        k1 = min(k0 + _SLAB, nk)
        sel = after_body[k0:k1] & (_world_axis(affine_ras, 2, shape, k0, k1) >= s_floor)
        if sel.any():
            front_after = max(
                front_after, float(_world_axis(affine_ras, 1, shape, k0, k1)[sel].max())
            )
    retreat = a_hi - front_after if math.isfinite(front_after) else math.inf
    metrics["anterior_retreat_mm"] = retreat
    if retreat < params.MIN_ANTERIOR_RETREAT_MM:
        failures.append("anterior_retreat_too_small")

    return VerifyReport(passed=not failures, failures=failures, metrics=metrics)


def _face_mask(
    affine: np.ndarray, shape: tuple[int, int, int], face_a: float, s_floor: float
) -> np.ndarray:
    out: np.ndarray = np.zeros(shape, dtype=bool)
    for k0 in range(0, shape[0], _SLAB):
        k1 = min(k0 + _SLAB, shape[0])
        out[k0:k1] = (_world_axis(affine, 1, shape, k0, k1) >= face_a) & (
            _world_axis(affine, 2, shape, k0, k1) >= s_floor
        )
    return out
