"""`geometric_v1` -- GEOMETRY.md "Option B" defacing, pure numpy/scipy, no randomness.

What it does (S7 plan section 3): inside a world-space slab covering the anterior part of the
head, remove every non-bone soft-tissue voxel that (a) lies within `strip_depth_mm` of the
outer body envelope and (b) is connected, through non-bone tissue, to the skin. Bone is never
touched and a flood cannot cross bone, so the brain stays wherever the skull is intact.

Why a strip and not a thin shell: skin, fat and muscle over the face are 3-15 mm thick (the
nose 25-30 mm with no bone beneath). Zeroing "a few mm" leaves a new face-shaped surface at
depth -- still recognisable in a `skin_surface` mesh. Removing everything down to bone leaves
the facial skeleton, not a face.

Every voxel outside the region mask, every bone voxel, and every voxel deeper than the strip
is bit-identical to the input. Anterior/superior are found in WORLD space from the affine
(see geometry.py) -- never from an array axis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from pipeline.common.errors import (
    DefaceAffineInvalidError,
    DefaceChangeImplausibleError,
    DefaceEfficacyCheckFailedError,
    DefaceNoEffectError,
    NoHeadFoundError,
)
from pipeline.stages.s7_deface import params
from pipeline.stages.s7_deface.geometry import (
    ANTERIOR,
    RIGHT,
    SUPERIOR,
    WorldFrame,
    is_orthonormal_isotropic,
    masked_extent,
    slabs,
    voxel_bbox,
)


@dataclass
class DefaceResult:
    defaced: np.ndarray
    region_mask: np.ndarray
    changed_mask: np.ndarray
    stats: dict[str, object]


def body_mask(volume: np.ndarray, tissue_min_hu: float) -> np.ndarray:
    """Largest connected tissue component, holes filled in 3-D (so sinus air counts as
    interior). Opening drops speckle and thin attachments (tubes, wires)."""
    tissue = volume >= tissue_min_hu
    opened = ndimage.binary_opening(tissue)
    labels, n = ndimage.label(opened, structure=np.ones((3, 3, 3)))
    del opened
    if n == 0:
        raise NoHeadFoundError({"reason": "no_tissue_voxels", "tissue_min_hu": tissue_min_hu})
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    largest = labels == int(sizes.argmax())
    del labels
    # opening also trims real protrusion tips (nose); grow back a bounded shell of real tissue
    restored = ndimage.binary_dilation(largest, iterations=params.BODY_RESTORE_ITERATIONS) & tissue
    return np.asarray(ndimage.binary_fill_holes(restored), dtype=bool)


def deface_region(
    volume: np.ndarray,
    affine_ras: np.ndarray,
    *,
    air_value: float,
    spacing_mm: float = 1.0,
    anterior_fraction: float = params.ANTERIOR_FRACTION,
    superior_fraction: float = params.SUPERIOR_FRACTION,
    head_height_cap_mm: float = params.HEAD_HEIGHT_CAP_MM,
    strip_depth_mm: float = params.STRIP_DEPTH_MM,
    seed_depth_mm: float = params.SEED_DEPTH_MM,
    bone_floor_hu: float = params.BONE_FLOOR_HU,
    tissue_min_hu: float = params.TISSUE_MIN_HU,
) -> DefaceResult:
    """GEOMETRY.md's `deface_region`, in world space. Returns the defaced copy, the region
    mask R, the changed mask, and stats. Never mutates `volume`. Fails closed."""
    if volume.ndim != 3 or not np.issubdtype(volume.dtype, np.floating):
        raise DefaceAffineInvalidError(
            {"reason": "volume_not_3d_float", "shape": list(volume.shape)}
        )
    if not np.isfinite(volume).all():
        raise DefaceAffineInvalidError({"reason": "volume_has_non_finite_values"})
    if not is_orthonormal_isotropic(affine_ras, spacing_mm):
        raise DefaceAffineInvalidError(
            {"reason": "affine_not_orthonormal_isotropic", "spacing_mm": spacing_mm}
        )

    shape = (int(volume.shape[0]), int(volume.shape[1]), int(volume.shape[2]))
    frame = WorldFrame.from_affine(affine_ras)
    body = body_mask(volume, tissue_min_hu)

    # --- head extents, in world space --------------------------------------------------------
    s_ext = masked_extent(frame, body, SUPERIOR)
    if s_ext is None:  # pragma: no cover - body_mask already raised on empty
        raise NoHeadFoundError({"reason": "empty_body"})
    s_min, s_max = s_ext
    s_head_lo = s_max - head_height_cap_mm
    s_top_lo = s_max - params.HEAD_TOP_SLAB_MM
    a_lo_e, a_hi_e = math.inf, -math.inf
    r_lo_top, r_hi_top = math.inf, -math.inf
    for k0, k1 in slabs(shape[0]):
        sel = body[k0:k1]
        if not sel.any():
            continue
        s_field = frame.field(SUPERIOR, shape, k0, k1)
        head = sel & (s_field >= s_head_lo)
        if head.any():
            a_vals = frame.field(ANTERIOR, shape, k0, k1)[head]
            a_lo_e, a_hi_e = min(a_lo_e, float(a_vals.min())), max(a_hi_e, float(a_vals.max()))
        top = sel & (s_field >= s_top_lo)
        if top.any():
            r_vals = frame.field(RIGHT, shape, k0, k1)[top]
            r_lo_top = min(r_lo_top, float(r_vals.min()))
            r_hi_top = max(r_hi_top, float(r_vals.max()))
    if a_lo_e > a_hi_e:
        raise NoHeadFoundError({"reason": "empty_head_slab"})

    body_voxels = int(body.sum())
    body_ml = body_voxels * spacing_mm**3 / 1000.0
    ap_extent = a_hi_e - a_lo_e
    s_extent = s_max - s_min
    head_width = r_hi_top - r_lo_top
    bone_in_body = int(((volume >= bone_floor_hu) & body).sum())
    sanity = {
        "body_volume_ml": body_ml,
        "ap_extent_mm": ap_extent,
        "s_extent_mm": s_extent,
        "head_width_mm": head_width,
        "bone_voxels": bone_in_body,
    }
    failed = []
    if body_ml < params.MIN_BODY_VOLUME_ML:
        failed.append("body_volume_too_small")
    if not (params.AP_EXTENT_RANGE_MM[0] <= ap_extent <= params.AP_EXTENT_RANGE_MM[1]):
        failed.append("ap_extent_out_of_range")
    if s_extent < params.MIN_S_EXTENT_MM:
        failed.append("s_extent_too_small")
    if not (params.HEAD_WIDTH_RANGE_MM[0] <= head_width <= params.HEAD_WIDTH_RANGE_MM[1]):
        failed.append("head_width_out_of_range")
    if bone_in_body < params.MIN_BONE_VOXELS:
        failed.append("too_little_bone")
    if failed:
        raise NoHeadFoundError({"reason": "sanity_check_failed", "failed": failed, **sanity})

    # --- region R: world slab -----------------------------------------------------------------
    a_lo = a_hi_e - anterior_fraction * ap_extent
    s_lo = s_max - min(superior_fraction * s_extent, head_height_cap_mm)
    region: np.ndarray = np.zeros(shape, dtype=bool)
    for k0, k1 in slabs(shape[0]):
        region[k0:k1] = (frame.field(ANTERIOR, shape, k0, k1) >= a_lo) & (
            frame.field(SUPERIOR, shape, k0, k1) >= s_lo
        )

    bbox = voxel_bbox(region & body)
    if bbox is None:
        raise NoHeadFoundError({"reason": "region_empty", "a_lo_mm": a_lo, "s_lo_mm": s_lo})
    margin = int(math.ceil(strip_depth_mm / spacing_mm)) + 2
    crop = tuple(
        slice(max(lo - margin, 0), min(hi + margin, n - 1) + 1)
        for (lo, hi), n in zip(bbox, shape, strict=True)
    )

    # A voxel's true depth <= strip_depth implies its nearest air voxel lies inside this
    # crop, so classification "depth <= strip_depth" is exact within it.
    v = volume[crop]
    b = body[crop]
    r = region[crop]
    depth = ndimage.distance_transform_edt(b, sampling=spacing_mm)
    cand = r & b & (v >= tissue_min_hu) & (v < bone_floor_hu) & (depth <= strip_depth_mm)
    seed = cand & (depth <= seed_depth_mm)
    labels, n_labels = ndimage.label(cand, structure=np.ones((3, 3, 3)))
    if n_labels:
        seed_labels = np.unique(labels[seed])
        seed_labels = seed_labels[seed_labels > 0]
        keep = np.isin(labels, seed_labels)
    else:
        keep = np.zeros_like(cand)
    del depth, labels, cand, seed

    defaced = volume.copy()
    defaced_crop = defaced[crop]
    defaced_crop[keep] = air_value
    changed: np.ndarray = np.zeros(shape, dtype=bool)
    changed[crop] = keep

    # --- postconditions (fail closed) ---------------------------------------------------------
    n_changed = int(changed.sum())
    detail = {"changed_voxels": n_changed, "body_voxels": body_voxels}
    if (changed & ~region).any():
        raise DefaceEfficacyCheckFailedError({"invariant": "changed_outside_region", **detail})
    if ((volume >= bone_floor_hu) & changed).any():
        raise DefaceEfficacyCheckFailedError({"invariant": "bone_changed", **detail})
    if n_changed < params.MIN_CHANGED_VOXELS:
        raise DefaceNoEffectError({**detail, "min_changed_voxels": params.MIN_CHANGED_VOXELS})
    if n_changed > params.MAX_CHANGED_FRACTION_OF_BODY * body_voxels:
        raise DefaceChangeImplausibleError(
            {**detail, "max_fraction": params.MAX_CHANGED_FRACTION_OF_BODY}
        )

    stats: dict[str, object] = {
        **sanity,
        "body_voxels": body_voxels,
        "head_extent_mm": {"A": [a_lo_e, a_hi_e], "S": [s_min, s_max]},
        "region_world_mm": {"a_min": a_lo, "s_min": s_lo},
        "region_voxels": int(region.sum()),
        "crop_zyx": [[int(sl.start), int(sl.stop) - 1] for sl in crop],
        "changed_voxels": n_changed,
        "changed_fraction_of_body": n_changed / body_voxels,
    }
    return DefaceResult(defaced=defaced, region_mask=region, changed_mask=changed, stats=stats)
