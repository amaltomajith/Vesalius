"""World-space helpers against hand-computed literals for five orientations. The S6 lesson
applies: the affine maps INDEX order (i=col, j=row, k=slice) while the array is (k, j, i);
a shortcut like "anterior = high row index" is mirrored on S6's default output.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.stages.s7_deface.geometry import (
    ANTERIOR,
    RIGHT,
    SUPERIOR,
    WorldFrame,
    is_orthonormal_isotropic,
    masked_extent,
    slabs,
    voxel_bbox,
    world_bounds,
)


def _aff(rows: list[list[float]]) -> np.ndarray:
    return np.array([*rows, [0.0, 0.0, 0.0, 1.0]])


# name -> (RAS affine mapping (i,j,k)->RAS, array index (k,j,i), hand-computed world point,
#          anterior coefficients (c_k,c_j,c_i), superior coefficients (c_k,c_j,c_i))
CASES = {
    # S6's default output: LPS-native volume written as RAS = diag(-1,-1,1).
    # x=-i+50, y=-j+60, z=k-70   ->   (k=2,j=3,i=4) = (46, 57, -68). Anterior = LOW row index.
    "lps_native_default": (
        _aff([[-1, 0, 0, 50], [0, -1, 0, 60], [0, 0, 1, -70]]),
        (2, 3, 4),
        (46.0, 57.0, -68.0),
        (0.0, -1.0, 0.0),
        (1.0, 0.0, 0.0),
    ),
    # x=i-50, y=j-60, z=k-70   ->   (-46, -57, -68). Anterior = HIGH row index.
    "ras_native": (
        _aff([[1, 0, 0, -50], [0, 1, 0, -60], [0, 0, 1, -70]]),
        (2, 3, 4),
        (-46.0, -57.0, -68.0),
        (0.0, 1.0, 0.0),
        (1.0, 0.0, 0.0),
    ),
    # z reversed: x=-i+50, y=-j+60, z=-k+80   ->   (46, 57, 78). Superior = LOW slice index.
    "z_reversed": (
        _aff([[-1, 0, 0, 50], [0, -1, 0, 60], [0, 0, -1, 80]]),
        (2, 3, 4),
        (46.0, 57.0, 78.0),
        (0.0, -1.0, 0.0),
        (-1.0, 0.0, 0.0),
    ),
    # coronal-like axis permutation: x=i-50, y=k-60, z=-j+70   ->   (-46, -58, 67).
    # Anterior is the SLICE axis here -- no row/column axis is anterior at all.
    "axis_permuted": (
        _aff([[1, 0, 0, -50], [0, 0, 1, -60], [0, -1, 0, 70]]),
        (2, 3, 4),
        (-46.0, -58.0, 67.0),
        (1.0, 0.0, 0.0),
        (0.0, -1.0, 0.0),
    ),
    # tilted (3-4-5 rotation): x=i, y=-0.8j-0.6k+10, z=-0.6j+0.8k+20
    # (k=5,j=10,i=3) -> (3, -1, 18). Anterior/superior mix two array axes.
    "tilted": (
        _aff([[1, 0, 0, 0], [0, -0.8, -0.6, 10], [0, -0.6, 0.8, 20]]),
        (5, 10, 3),
        (3.0, -1.0, 18.0),
        (-0.6, -0.8, 0.0),
        (0.8, -0.6, 0.0),
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_index_to_world_matches_the_hand_computed_literal(name: str) -> None:
    affine, kji, world, _, _ = CASES[name]
    assert np.allclose(WorldFrame.from_affine(affine).point(kji), world)


@pytest.mark.parametrize("name", sorted(CASES))
def test_anterior_and_superior_coefficients_are_per_array_axis_in_kji_order(name: str) -> None:
    affine, _, _, anterior, superior = CASES[name]
    frame = WorldFrame.from_affine(affine)
    assert np.allclose(frame.coefficients(ANTERIOR), anterior)
    assert np.allclose(frame.coefficients(SUPERIOR), superior)


def test_on_the_default_s6_output_anterior_is_the_low_row_index() -> None:
    frame = WorldFrame.from_affine(CASES["lps_native_default"][0])
    a = frame.point((0, 0, 0))[ANTERIOR]
    assert frame.point((0, 1, 0))[ANTERIOR] < a  # one row further -> more posterior


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("axis", [RIGHT, ANTERIOR, SUPERIOR])
def test_field_equals_brute_force_over_the_affine_in_its_native_index_order(
    name: str, axis: int
) -> None:
    affine = CASES[name][0]
    shape = (19, 6, 7)  # 19 slices: several full slabs plus a remainder
    frame = WorldFrame.from_affine(affine)
    k, j, i = np.indices(shape).astype(float)
    # independent of WorldFrame's column reversal: uses the affine in its native (i, j, k) order
    expected = affine[axis, 0] * i + affine[axis, 1] * j + affine[axis, 2] * k + affine[axis, 3]
    got = np.concatenate([frame.field(axis, shape, k0, k1) for k0, k1 in slabs(shape[0])])
    assert np.allclose(got, expected)


@pytest.mark.parametrize("name", sorted(CASES))
def test_masked_extent_and_world_bounds_equal_brute_force(name: str) -> None:
    affine = CASES[name][0]
    shape = (19, 8, 9)
    rng = np.random.default_rng(3)
    mask = rng.random(shape) > 0.8
    frame = WorldFrame.from_affine(affine)
    k, j, i = np.indices(shape).astype(float)
    bounds = world_bounds(frame, mask)
    assert bounds is not None
    for name_, axis in (("R", RIGHT), ("A", ANTERIOR), ("S", SUPERIOR)):
        full = affine[axis, 0] * i + affine[axis, 1] * j + affine[axis, 2] * k + affine[axis, 3]
        assert np.allclose(bounds[name_], [full[mask].min(), full[mask].max()])
        lo, hi = masked_extent(frame, mask, axis)  # type: ignore[misc]
        assert (lo, hi) == pytest.approx((full[mask].min(), full[mask].max()))


def test_empty_mask_has_no_extent_or_bbox() -> None:
    frame = WorldFrame.from_affine(CASES["ras_native"][0])
    empty = np.zeros((4, 4, 4), dtype=bool)
    assert masked_extent(frame, empty, ANTERIOR) is None
    assert world_bounds(frame, empty) is None
    assert voxel_bbox(empty) is None


def test_voxel_bbox_is_in_array_index_order() -> None:
    mask = np.zeros((6, 7, 8), dtype=bool)
    mask[1:3, 4:6, 2:7] = True
    assert voxel_bbox(mask) == [[1, 2], [4, 5], [2, 6]]


def test_slabs_cover_every_slice_exactly_once() -> None:
    covered = [k for k0, k1 in slabs(19) for k in range(k0, k1)]
    assert covered == list(range(19))


def test_orthonormal_isotropic_accepts_flips_permutations_and_tilts() -> None:
    for name in CASES:
        assert is_orthonormal_isotropic(CASES[name][0], 1.0), name


def test_orthonormal_isotropic_rejects_anisotropic_sheared_and_wrong_spacing() -> None:
    assert not is_orthonormal_isotropic(_aff([[1, 0, 0, 0], [0, 0.5, 0, 0], [0, 0, 1, 0]]), 1.0)
    assert not is_orthonormal_isotropic(_aff([[1, 0.3, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]), 1.0)
    assert not is_orthonormal_isotropic(_aff([[2, 0, 0, 0], [0, 2, 0, 0], [0, 0, 2, 0]]), 1.0)


def test_from_affine_rejects_a_non_4x4_matrix() -> None:
    with pytest.raises(ValueError):
        WorldFrame.from_affine(np.eye(3))


def test_orthonormal_isotropic_rejects_unit_length_but_non_orthogonal_columns() -> None:
    # every column has length exactly 1, so only the orthogonality check can reject this
    sheared = _aff([[1, 0.6, 0, 0], [0, 0.8, 0, 0], [0, 0, 1, 0]])
    assert np.allclose(np.linalg.norm(sheared[:3, :3], axis=0), 1.0)
    assert not is_orthonormal_isotropic(sheared, 1.0)
