"""S9 -- pure marching-cubes surface extraction (GEOMETRY.md "Surface extraction", STAGES.md S9).
No randomness, no DB, no I/O.

**Why the mask confines but does not directly feed marching cubes:** STAGES.md says "In: binary
mask + spacing"; GEOMETRY.md says pass the intensity volume with a level, "not a binary mask" --
a mask alone produces blocky, stair-stepped output. Both are right about different things: the
mask says WHERE a surface may legitimately be found (so marching cubes doesn't rediscover table
fragments or speckle S8's cleanup already discarded); the actual isosurface comes from
INTENSITY data so it interpolates smoothly. The mask is lightly dilated first so the isosurface
has real neighbouring intensity to interpolate against, rather than an abrupt substituted
background everywhere the (morphologically cleaned) mask boundary doesn't already coincide with
a natural threshold crossing. Residual, documented: where cleanup DID move the mask boundary away
from the raw threshold, the surface there is influenced by the substitution and can look more
like the blocky mask edge than a smooth isosurface -- an accepted tradeoff of masked marching
cubes, not a bug.

**The axis/affine trap, corrected beyond GEOMETRY.md's literal snippet:** `marching_cubes`'s
`spacing=` argument only SCALES each axis -- no rotation, no origin translation. Passing it alone
(as GEOMETRY.md's example does) leaves vertices in a flipped/offset local grid for an
axis-aligned volume, and is outright wrong for a tilted one. Here `spacing` is never passed
(vertices stay in raw voxel-index units, columns in the array's own (k, j, i) order); the FULL
affine (`meta.spatial.affine`, RAS mm) is applied exactly once afterwards, one matrix multiply,
reusing the S6/S7-verified convention that the affine maps index order (i=col, j=row, k=slice)
while the array and marching-cubes' vertex columns are (k, j, i) -- the column order is reversed
before the multiply, never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from skimage import measure

from pipeline.common.errors import (
    SurfaceAffineInvalidError,
    SurfaceGeometryMismatchError,
    SurfaceInvariantViolationError,
    SurfaceZeroFacesError,
)
from pipeline.stages.s7_deface.geometry import is_orthonormal_isotropic


@dataclass
class MeshResult:
    vertices_ras: np.ndarray  # (n, 3) float64, RAS mm
    faces: np.ndarray  # (m, 3) int64, indices into vertices_ras
    stats: dict[str, object]


def index_vertices_to_world(verts_kji: np.ndarray, affine_ras: np.ndarray) -> np.ndarray:
    """marching_cubes' vertex columns are in the array's own (k, j, i) order (matching its
    `(z, y, x)`-shaped input); the affine maps index order (i=col, j=row, k=slice). Reversing
    the columns before the multiply -- never assumed -- is the S6/S7-verified convention this
    project uses everywhere index meets world space."""
    ijk1 = verts_kji[:, ::-1]
    return np.asarray(ijk1 @ affine_ras[:3, :3].T + affine_ras[:3, 3])


def _world_bbox(
    shape: tuple[int, int, int], affine_ras: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """The volume's own physical bounding box: every corner of index space, transformed."""
    nk, nj, ni = shape
    corners_kji = np.array(
        [[k, j, i] for k in (0, nk - 1) for j in (0, nj - 1) for i in (0, ni - 1)], dtype=float
    )
    world = index_vertices_to_world(corners_kji, affine_ras)
    return world.min(axis=0), world.max(axis=0)


def extract_mesh(
    volume: np.ndarray,
    mask: np.ndarray,
    affine_ras: np.ndarray,
    *,
    level: float,
    air_value: float,
    spacing_mm: float = 1.0,
    dilation_voxels: int,
    step_size: int,
    world_bbox_tolerance_mm: float,
) -> MeshResult:
    """Never mutates `volume`/`mask`. Fails closed on zero faces or a postcondition violation."""
    if volume.shape != mask.shape:
        raise SurfaceGeometryMismatchError(
            {
                "reason": "shape_mismatch",
                "volume_shape": list(volume.shape),
                "mask_shape": list(mask.shape),
            }
        )
    if not is_orthonormal_isotropic(affine_ras, spacing_mm):
        raise SurfaceAffineInvalidError({"reason": "affine_not_orthonormal_isotropic"})

    # scipy footgun: `binary_dilation(..., iterations=0)` does NOT mean "no dilation" -- it
    # means "repeat until the result stops changing", which for a bounded array fills nearly
    # everything reachable. 0 must be handled as a real, literal no-op.
    confine = (
        mask.astype(bool)
        if dilation_voxels == 0
        else ndimage.binary_dilation(mask.astype(bool), iterations=dilation_voxels)
    )
    masked_volume = np.where(confine, volume, air_value).astype(np.float64)

    try:
        verts, faces, _normals, _values = measure.marching_cubes(
            masked_volume, level=level, step_size=step_size, allow_degenerate=False
        )
    except (RuntimeError, ValueError) as exc:
        # marching_cubes raises when the level never crosses (all-above or all-below) -- "no
        # surface at all" and "surface but zero faces" are indistinguishable to the caller
        raise SurfaceZeroFacesError(
            {"reason": "no_level_crossing", "level": level, "error": str(exc)}
        ) from exc

    if len(faces) == 0:
        raise SurfaceZeroFacesError({"reason": "zero_faces", "level": level})

    world = index_vertices_to_world(verts, affine_ras)

    lo, hi = _world_bbox(volume.shape, affine_ras)
    tol = world_bbox_tolerance_mm
    if (world < lo - tol).any() or (world > hi + tol).any():
        raise SurfaceInvariantViolationError(
            {
                "reason": "vertex_outside_world_bbox",
                "bbox_min": lo.tolist(),
                "bbox_max": hi.tolist(),
                "vertex_min": world.min(axis=0).tolist(),
                "vertex_max": world.max(axis=0).tolist(),
            }
        )
    if faces.max(initial=-1) >= len(world):
        raise SurfaceInvariantViolationError({"reason": "face_index_out_of_range"})

    stats: dict[str, object] = {
        "vertex_count": int(len(world)),
        "face_count": int(len(faces)),
        "confine_voxels": int(confine.sum()),
        "level": level,
        "dilation_voxels": dilation_voxels,
        "step_size": step_size,
        "world_bbox_min": lo.tolist(),
        "world_bbox_max": hi.tolist(),
        "vertex_world_min": world.min(axis=0).tolist(),
        "vertex_world_max": world.max(axis=0).tolist(),
    }
    return MeshResult(vertices_ras=world, faces=faces.astype(np.int64), stats=stats)
