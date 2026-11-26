"""Analytic synthetic scans, defined in RAS world millimetres (x right, y anterior, z superior)
and evaluated on arbitrary coordinate grids -- so the same physical object can be sampled onto
any slice geometry with no interpolation.

The head is the phantom the project's defacing tests were tuned on (ported from
tests/fixtures/head_phantom.py; the constants are identical): a head ellipsoid with a skull shell,
brain, a soft face with a nose, a facial bone block containing an air sinus, chin, neck and a
vertebra, and a skull-base slab. The chest is new: a body ellipse with two lungs, spine, a rib
cage and a sternum.

WHAT THESE CAN AND CANNOT SHOW: that the pipeline's mechanisms work end to end. They are not
evidence about real anatomy, real noise, or clinical performance.
"""

from __future__ import annotations

import math

import numpy as np

AIR = -1000.0
SOFT = 40.0
BRAIN = 35.0
BONE = 1000.0
LUNG = -800.0
CANCELLOUS = 350.0

# extents (RAS mm): (xmin, xmax), (ymin, ymax), (zmin, zmax)
HEAD_EXTENT = ((-75.0, 75.0), (-98.0, 130.0), (-105.0, 105.0))
CHEST_EXTENT = ((-135.0, 135.0), (-88.0, 88.0), (-50.0, 50.0))

_HEAD_C, _HEAD_R = (0.0, 0.0, 0.0), (70.0, 90.0, 100.0)
_FACE_SOFT = ((0.0, 70.0, -35.0), (45.0, 42.0, 42.0))
_CHIN_SOFT = ((0.0, 62.0, -80.0), (34.0, 30.0, 22.0))
_NOSE = ((0.0, 105.0, -10.0), (9.0, 18.0, 22.0))
_FACE_BONE = ((0.0, 62.0, -35.0), (32.0, 26.0, 30.0))
_CHIN_BONE = ((0.0, 55.0, -80.0), (28.0, 22.0, 12.0))
_SINUS = ((0.0, 66.0, -30.0), (12.0, 10.0, 12.0))
_SKULL_BASE = ((0.0, -35.0, -88.0), (30.0, 25.0, 8.0))

NOSE_TIP_RAS_MM = (0.0, 123.0, -10.0)  # front of the phantom's nose, used by checks and figures


def _ell(x, y, z, c, r, shrink=0.0):  # type: ignore[no-untyped-def]
    return ((x - c[0]) / (r[0] - shrink)) ** 2 + ((y - c[1]) / (r[1] - shrink)) ** 2 + (
        (z - c[2]) / (r[2] - shrink)
    ) ** 2 <= 1.0


def head_hu(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """HU of the synthetic head at RAS coordinates (broadcastable arrays)."""
    head = _ell(x, y, z, _HEAD_C, _HEAD_R)
    neck = (x**2 + (y + 10.0) ** 2 <= 50.0**2) & (z >= -200.0) & (z <= -80.0)
    soft_all = (
        head
        | neck
        | _ell(x, y, z, *_FACE_SOFT)
        | _ell(x, y, z, *_CHIN_SOFT)
        | _ell(x, y, z, *_NOSE)
    )
    hu = np.full(np.broadcast(x, y, z).shape, AIR, dtype=np.float32)
    hu[soft_all] = SOFT
    inner = _ell(x, y, z, _HEAD_C, _HEAD_R, shrink=12.0)
    skull = _ell(x, y, z, _HEAD_C, _HEAD_R, shrink=5.0) & ~inner
    hu[skull] = BONE
    hu[inner] = BRAIN
    vertebra = (x**2 + (y + 25.0) ** 2 <= 12.0**2) & (z >= -260.0) & (z <= -90.0)
    bone = (
        skull
        | _ell(x, y, z, *_FACE_BONE)
        | _ell(x, y, z, *_CHIN_BONE)
        | vertebra
        | _ell(x, y, z, *_SKULL_BASE)
    )
    hu[bone] = BONE
    hu[_ell(x, y, z, *_SINUS)] = AIR
    return hu


def chest_hu(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    """HU of the synthetic chest at RAS coordinates: body, lungs, spine, ribs, sternum."""
    body = (x / 125.0) ** 2 + (y / 80.0) ** 2 <= 1.0
    hu = np.full(np.broadcast(x, y, z).shape, AIR, dtype=np.float32)
    hu[body] = SOFT

    left = _ell(x, y, z, (-58.0, 4.0, 0.0), (46.0, 58.0, 56.0))
    right = _ell(x, y, z, (58.0, 4.0, 0.0), (46.0, 58.0, 56.0))
    hu[(left | right) & body] = LUNG

    # rib cage: an elliptical shell just inside the skin, interrupted every 18 mm along z
    r2 = (x / 114.0) ** 2 + (y / 72.0) ** 2
    ring = (r2 <= 1.0) & (r2 >= (1.0 - 12.0 / 100.0) ** 2)
    ribs = ring & (np.cos(2.0 * math.pi * z / 18.0) > 0.0) & (y > -60.0)
    hu[ribs] = BONE * 0.55

    spine_r2 = x**2 + (y + 52.0) ** 2
    hu[(spine_r2 <= 14.0**2) & body] = CANCELLOUS
    hu[(spine_r2 <= 14.0**2) & (spine_r2 >= 10.0**2)] = BONE * 0.8
    sternum = _ell(x, y, z, (0.0, 66.0, 0.0), (12.0, 5.0, 50.0))
    hu[sternum] = CANCELLOUS
    return hu
