"""Analytic synthetic head phantom, defined in RAS WORLD millimetres and rendered onto ANY
grid (shape + affine). Every orientation variant is therefore the same physical object with
exact ground-truth masks and no interpolation, so code that assumes an array axis is anterior
fails on the variants that disagree.

World coordinates here are computed from the raw affine in its native (i, j, k) order with
NumPy meshgrids -- deliberately NOT via s7_deface.geometry.WorldFrame, so a bug in S7's
geometry cannot be mirrored in its own fixture.

Layout (x right, y anterior, z superior; head centred on the origin, face toward +y):
  head ellipsoid 70/90/100 mm, 40 HU        skull shell 5-12 mm inside the skin, 1000 HU
  brain 35 HU                               soft face + cheeks (ellipsoid), nose (no bone)
  facial bone block (maxilla) with a CLOSED air sinus inside it; chin soft tissue + chin bone
  neck stub with a vertebra; skull-base bone slab (posterior/inferior, outside the face zone)
  optional shoulders slab far below the head (tests the head-height cap)

WHAT IT CAN AND CANNOT SHOW: orientation-independence, the bone shield, outside-region
preservation, that the parameters remove a face-shaped protrusion at all. It cannot show
effectiveness on noise, beam hardening, dental artefacts, thin-bone partial volume, hair, ears,
head holders, obese faces or contrast. It is a regression/correctness fixture, NOT evidence of
real de-identification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

AIR = -1000.0
SOFT = 40.0
BRAIN = 35.0
BONE = 1000.0

# world bounding box of the head-only phantom (mm): (xmin, xmax), (ymin, ymax), (zmin, zmax)
HEAD_BBOX = ((-75.0, 75.0), (-98.0, 130.0), (-105.0, 105.0))
SHOULDER_BBOX = ((-150.0, 150.0), (-98.0, 130.0), (-280.0, 105.0))

# Rotations whose COLUMNS are the world direction of array index axes (i, j, k). All proper
# (det +1), like every S6 output.
_RZ = math.radians(10.0)
_RX = math.radians(20.0)
_ROT_X = np.array(
    [[1, 0, 0], [0, math.cos(_RX), -math.sin(_RX)], [0, math.sin(_RX), math.cos(_RX)]]
)
_ROT_Z = np.array(
    [[math.cos(_RZ), -math.sin(_RZ), 0], [math.sin(_RZ), math.cos(_RZ), 0], [0, 0, 1]]
)
ROTATIONS: dict[str, np.ndarray] = {
    # S6's default output: LPS-native volume written as RAS -> i=-x, j=-y, k=+z
    "lps_native_default": np.diag([-1.0, -1.0, 1.0]),
    "ras_native": np.eye(3),
    "z_reversed": np.diag([1.0, -1.0, -1.0]),
    # coronal-like: i=+x, j=-z, k=+y -- no row/column axis is anterior at all
    "axis_permuted": np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]),
    "tilted": np.diag([-1.0, -1.0, 1.0]) @ _ROT_X @ _ROT_Z,
}


@dataclass(frozen=True)
class Grid:
    shape: tuple[int, int, int]  # (nk, nj, ni) -- array order
    affine_ras: np.ndarray  # 4x4, maps index (i, j, k) -> RAS mm


def grid_for(
    rotation: np.ndarray,
    bbox: tuple[tuple[float, float], ...] = HEAD_BBOX,
    origin_shift: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Grid:
    """1 mm grid oriented by `rotation` that just covers the world bounding box."""
    corners = np.array([[x, y, z] for x in bbox[0] for y in bbox[1] for z in bbox[2]], dtype=float)
    idx = corners @ rotation  # rotation is orthonormal, so R^T p == p @ R
    lo, hi = idx.min(axis=0), idx.max(axis=0)
    origin = rotation @ lo + np.asarray(origin_shift, dtype=float)
    ni, nj, nk = (int(math.ceil(hi[a] - lo[a])) + 1 for a in range(3))
    affine = np.eye(4)
    affine[:3, :3] = rotation
    affine[:3, 3] = origin
    return Grid(shape=(nk, nj, ni), affine_ras=affine)


def _ell(x, y, z, c, r, shrink=0.0):  # type: ignore[no-untyped-def]
    return ((x - c[0]) / (r[0] - shrink)) ** 2 + ((y - c[1]) / (r[1] - shrink)) ** 2 + (
        (z - c[2]) / (r[2] - shrink)
    ) ** 2 <= 1.0


HEAD_C, HEAD_R = (0.0, 0.0, 0.0), (70.0, 90.0, 100.0)
FACE_SOFT = ((0.0, 70.0, -35.0), (45.0, 42.0, 42.0))
CHIN_SOFT = ((0.0, 62.0, -80.0), (34.0, 30.0, 22.0))
NOSE = ((0.0, 105.0, -10.0), (9.0, 18.0, 22.0))
FACE_BONE = ((0.0, 62.0, -35.0), (32.0, 26.0, 30.0))
CHIN_BONE = ((0.0, 55.0, -80.0), (28.0, 22.0, 12.0))
SINUS = ((0.0, 66.0, -30.0), (12.0, 10.0, 12.0))
SKULL_BASE = ((0.0, -35.0, -88.0), (30.0, 25.0, 8.0))


@dataclass
class Phantom:
    volume: np.ndarray  # float32 HU, array order (k, j, i)
    grid: Grid
    truth: dict[str, np.ndarray]  # analytic ground-truth masks on the same grid


def render(grid: Grid, *, shoulders: bool = False) -> Phantom:
    nk, nj, ni = grid.shape
    a = grid.affine_ras
    volume = np.full(grid.shape, AIR, dtype=np.float32)
    truth = {
        name: np.zeros(grid.shape, dtype=bool)
        for name in ("nose_only", "brain", "bone", "sinus", "skull_base", "face_soft")
    }
    for k0 in range(0, nk, 8):
        k1 = min(k0 + 8, nk)
        kk, jj, ii = np.meshgrid(
            np.arange(k0, k1, dtype=np.float64),
            np.arange(nj, dtype=np.float64),
            np.arange(ni, dtype=np.float64),
            indexing="ij",
        )
        x = a[0, 0] * ii + a[0, 1] * jj + a[0, 2] * kk + a[0, 3]
        y = a[1, 0] * ii + a[1, 1] * jj + a[1, 2] * kk + a[1, 3]
        z = a[2, 0] * ii + a[2, 1] * jj + a[2, 2] * kk + a[2, 3]

        head = _ell(x, y, z, HEAD_C, HEAD_R)
        neck = (x**2 + (y + 10.0) ** 2 <= 50.0**2) & (z >= -200.0) & (z <= -80.0)
        soft = head | neck
        if shoulders:
            soft |= (
                (np.abs(x) <= 140.0) & (y >= -60.0) & (y <= 40.0) & (z >= -260.0) & (z <= -190.0)
            )
        face_soft = _ell(x, y, z, *FACE_SOFT)
        chin_soft = _ell(x, y, z, *CHIN_SOFT)
        nose = _ell(x, y, z, *NOSE)
        soft_all = soft | face_soft | chin_soft | nose

        hu = np.full(x.shape, AIR)
        hu[soft_all] = SOFT
        inner = _ell(x, y, z, HEAD_C, HEAD_R, shrink=12.0)
        skull = _ell(x, y, z, HEAD_C, HEAD_R, shrink=5.0) & ~inner
        hu[skull] = BONE
        brain = inner
        hu[brain] = BRAIN
        face_bone = _ell(x, y, z, *FACE_BONE)
        chin_bone = _ell(x, y, z, *CHIN_BONE)
        vertebra = (x**2 + (y + 25.0) ** 2 <= 12.0**2) & (z >= -260.0) & (z <= -90.0)
        base = _ell(x, y, z, *SKULL_BASE)
        bone = skull | face_bone | chin_bone | vertebra | base
        hu[bone] = BONE
        sinus = _ell(x, y, z, *SINUS)
        hu[sinus] = AIR

        volume[k0:k1] = hu.astype(np.float32)
        truth["nose_only"][k0:k1] = nose & ~(soft | face_soft | chin_soft | bone)
        truth["brain"][k0:k1] = hu == BRAIN
        truth["bone"][k0:k1] = hu == BONE
        truth["sinus"][k0:k1] = sinus
        truth["skull_base"][k0:k1] = base & ~sinus
        truth["face_soft"][k0:k1] = (face_soft | chin_soft | nose) & (hu == SOFT)
    return Phantom(volume=volume, grid=grid, truth=truth)


def render_variant(name: str, *, shoulders: bool = False) -> Phantom:
    bbox = SHOULDER_BBOX if shoulders else HEAD_BBOX
    return render(grid_for(ROTATIONS[name], bbox), shoulders=shoulders)
