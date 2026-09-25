"""`index_vertices_to_world` against hand-computed literals for five orientations -- the same
table `test_deface_geometry.py` uses, because this is the exact same trap restated at the mesh
boundary: marching_cubes' vertex columns are (k, j, i), the affine maps index order (i, j, k).
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.stages.s9_surface_extraction.mesh_extract import _world_bbox, index_vertices_to_world


def _aff(rows: list[list[float]]) -> np.ndarray:
    return np.array([*rows, [0.0, 0.0, 0.0, 1.0]])


# name -> (affine, verts in (k,j,i) order, hand-computed world points)
CASES = {
    # S6's default output: LPS-native volume written as RAS = diag(-1,-1,1).
    # x=-i+50, y=-j+60, z=k-70   ->   (k=2,j=3,i=4) = (46, 57, -68)
    "lps_native_default": (
        _aff([[-1, 0, 0, 50], [0, -1, 0, 60], [0, 0, 1, -70]]),
        [[2.0, 3.0, 4.0]],
        [[46.0, 57.0, -68.0]],
    ),
    # x=i-50, y=j-60, z=k-70   ->   (-46, -57, -68)
    "ras_native": (
        _aff([[1, 0, 0, -50], [0, 1, 0, -60], [0, 0, 1, -70]]),
        [[2.0, 3.0, 4.0]],
        [[-46.0, -57.0, -68.0]],
    ),
    # z reversed: x=-i+50, y=-j+60, z=-k+80   ->   (46, 57, 78)
    "z_reversed": (
        _aff([[-1, 0, 0, 50], [0, -1, 0, 60], [0, 0, -1, 80]]),
        [[2.0, 3.0, 4.0]],
        [[46.0, 57.0, 78.0]],
    ),
    # coronal-like axis permutation: x=i-50, y=k-60, z=-j+70   ->   (-46, -58, 67)
    "axis_permuted": (
        _aff([[1, 0, 0, -50], [0, 0, 1, -60], [0, -1, 0, 70]]),
        [[2.0, 3.0, 4.0]],
        [[-46.0, -58.0, 67.0]],
    ),
    # tilted (3-4-5 rotation): x=i, y=-0.8j-0.6k+10, z=-0.6j+0.8k+20
    # (k=5,j=10,i=3) -> (3, -1, 18)
    "tilted": (
        _aff([[1, 0, 0, 0], [0, -0.8, -0.6, 10], [0, -0.6, 0.8, 20]]),
        [[5.0, 10.0, 3.0]],
        [[3.0, -1.0, 18.0]],
    ),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_index_vertices_to_world_matches_the_hand_computed_literal(name: str) -> None:
    affine, verts_kji, expected = CASES[name]
    got = index_vertices_to_world(np.array(verts_kji), affine)
    assert np.allclose(got, expected)


@pytest.mark.parametrize("name", sorted(CASES))
def test_multiple_vertices_convert_independently_in_one_call(name: str) -> None:
    affine, verts_kji, expected = CASES[name]
    stacked = np.array(verts_kji * 3)
    got = index_vertices_to_world(stacked, affine)
    assert np.allclose(got, np.array(expected * 3))


def test_on_the_default_s6_output_a_higher_i_index_moves_toward_negative_x() -> None:
    """Documents the same "anterior is the low row index" lesson S7's geometry test pins down,
    restated for a raw vertex array: it is never safe to assume a coordinate axis maps
    monotonically the "obvious" way without checking the affine."""
    affine = CASES["lps_native_default"][0]
    a = index_vertices_to_world(np.array([[0.0, 0.0, 0.0]]), affine)[0]
    b = index_vertices_to_world(np.array([[0.0, 0.0, 1.0]]), affine)[0]
    assert b[0] < a[0]  # x decreases as i increases, under diag(-1,-1,1)


@pytest.mark.parametrize("name", sorted(CASES))
def test_world_bbox_contains_every_corner_of_index_space(name: str) -> None:
    affine = CASES[name][0]
    shape = (6, 7, 8)
    lo, hi = _world_bbox(shape, affine)
    nk, nj, ni = shape
    corners = np.array(
        [[k, j, i] for k in (0, nk - 1) for j in (0, nj - 1) for i in (0, ni - 1)], dtype=float
    )
    world = index_vertices_to_world(corners, affine)
    assert (world >= lo - 1e-9).all()
    assert (world <= hi + 1e-9).all()
    # and the bbox is TIGHT -- every face of it is touched by at least one corner
    assert np.allclose(world.min(axis=0), lo)
    assert np.allclose(world.max(axis=0), hi)
