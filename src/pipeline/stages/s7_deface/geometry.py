"""World-space helpers: where is anterior / superior for THIS volume?

Never assume an array axis. S6 writes arrays as (z, y, x) = (slice, row, column) while its
RAS affine maps INDEX order (i=column, j=row, k=slice); on the default S6 output the affine is
`diag(-1, -1, 1)` plus an origin, so array rows increase toward POSTERIOR -- anterior is the
LOW row index, and a "front = high y index" shortcut is mirrored. For tilted or axis-permuted
volumes no single array axis is anterior at all. Everything here therefore works from the
affine: a voxel's RAS position is a linear function of its array index,

    world = M @ [k, j, i] + t          (M = affine[:3, :3] with columns reversed)

so any world coordinate (R / A / S) of every voxel is a separable sum of three 1-D vectors.
It is computed per k-slab so no full 3-D coordinate array is ever materialised.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RIGHT = 0  # RAS +x
ANTERIOR = 1  # RAS +y
SUPERIOR = 2  # RAS +z

_SLAB = 8  # k-slices per chunk


@dataclass(frozen=True)
class WorldFrame:
    """Array index (k, j, i) -> RAS world (mm): p = M @ [k, j, i] + t."""

    M: np.ndarray  # (3, 3), columns ordered (k, j, i)
    t: np.ndarray  # (3,)

    @classmethod
    def from_affine(cls, affine_ras: np.ndarray) -> WorldFrame:
        a = np.asarray(affine_ras, dtype=float)
        if a.shape != (4, 4):
            raise ValueError(f"affine must be 4x4, got {a.shape}")
        # affine columns are (i, j, k); the array is indexed (k, j, i)
        return cls(M=a[:3, :3][:, ::-1].copy(), t=a[:3, 3].copy())

    def point(self, kji: tuple[float, float, float]) -> np.ndarray:
        return self.M @ np.asarray(kji, dtype=float) + self.t

    def coefficients(self, axis: int) -> tuple[float, float, float]:
        """(c_k, c_j, c_i): the change in world coordinate `axis` per array-index step."""
        row = self.M[axis]
        return float(row[0]), float(row[1]), float(row[2])

    def field(self, axis: int, shape: tuple[int, int, int], k0: int, k1: int) -> np.ndarray:
        """World coordinate `axis` of every voxel with k in [k0, k1), shape (k1-k0, nj, ni)."""
        _, nj, ni = shape
        ck, cj, ci = self.coefficients(axis)
        k: np.ndarray = np.arange(k0, k1, dtype=np.float64)[:, None, None]
        j: np.ndarray = np.arange(nj, dtype=np.float64)[None, :, None]
        i: np.ndarray = np.arange(ni, dtype=np.float64)[None, None, :]
        return ck * k + cj * j + ci * i + float(self.t[axis])


def slabs(nk: int, size: int = _SLAB) -> list[tuple[int, int]]:
    return [(k0, min(k0 + size, nk)) for k0 in range(0, nk, size)]


def masked_extent(frame: WorldFrame, mask: np.ndarray, axis: int) -> tuple[float, float] | None:
    """(min, max) of world coordinate `axis` over the True voxels of `mask`; None if empty."""
    lo, hi = np.inf, -np.inf
    for k0, k1 in slabs(mask.shape[0]):
        sel = mask[k0:k1]
        if not sel.any():
            continue
        values = frame.field(axis, mask.shape, k0, k1)[sel]
        lo, hi = min(lo, float(values.min())), max(hi, float(values.max()))
    return None if lo > hi else (lo, hi)


def world_bounds(frame: WorldFrame, mask: np.ndarray) -> dict[str, list[float]] | None:
    """RAS bounding box (mm) of the True voxels: {"R":[lo,hi], "A":[lo,hi], "S":[lo,hi]}."""
    out: dict[str, list[float]] = {}
    for name, axis in (("R", RIGHT), ("A", ANTERIOR), ("S", SUPERIOR)):
        ext = masked_extent(frame, mask, axis)
        if ext is None:
            return None
        out[name] = [ext[0], ext[1]]
    return out


def voxel_bbox(mask: np.ndarray) -> list[list[int]] | None:
    """Array-index bounding box [[k0,k1],[j0,j1],[i0,i1]] (inclusive) of the True voxels."""
    if not mask.any():
        return None
    axes = ((1, 2), (0, 2), (0, 1))
    out: list[list[int]] = []
    for axis in axes:
        idx = np.flatnonzero(mask.any(axis=axis))
        out.append([int(idx.min()), int(idx.max())])
    return out


def is_orthonormal_isotropic(affine_ras: np.ndarray, spacing_mm: float, tol: float = 1e-3) -> bool:
    """True if the affine's 3x3 part is `spacing_mm` times a rotation/flip (orthonormal
    columns, equal length)."""
    lin = np.asarray(affine_ras, dtype=float)[:3, :3]
    norms = np.linalg.norm(lin, axis=0)
    if not np.allclose(norms, spacing_mm, atol=tol):
        return False
    unit = lin / spacing_mm
    return bool(np.allclose(unit.T @ unit, np.eye(3), atol=tol))
