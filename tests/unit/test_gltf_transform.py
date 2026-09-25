"""`RAS_TO_GLTF` against hand-computed literals and the mirror-check that motivated it -- see
gltf_transform.py's module docstring for the full finding.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.stages.s11_export.gltf_transform import (
    MM_TO_M,
    RAS_TO_GLTF_3X3,
    centring_offset_mm,
    to_gltf_space,
)

# GEOMETRY.md's LITERAL matrix -- for LPS input. Used here only to prove applying it to RAS data
# mirrors the model (the bug this stage's design avoids).
_LPS_TO_GLTF_3X3 = np.array(
    [
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, 1.0, 0.0],
    ]
)


def test_ras_to_gltf_is_a_proper_rotation_not_a_reflection() -> None:
    """No mirroring by construction, not just by absence of a bug: determinant +1."""
    assert np.linalg.det(RAS_TO_GLTF_3X3) == pytest.approx(1.0)
    assert np.allclose(RAS_TO_GLTF_3X3 @ RAS_TO_GLTF_3X3.T, np.eye(3))  # orthonormal


@pytest.mark.parametrize(
    "ras, expected_gltf",
    [
        ((1.0, 0.0, 0.0), (1.0, 0.0, 0.0)),  # Right -> Right, no flip
        ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),  # Superior -> Up
        ((0.0, 1.0, 0.0), (0.0, 0.0, -1.0)),  # Anterior -> -Forward
        ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),  # Posterior -> +Forward
    ],
)
def test_ras_to_gltf_matches_geometry_mds_semantic_table(
    ras: tuple[float, float, float], expected_gltf: tuple[float, float, float]
) -> None:
    got = np.array(ras) @ RAS_TO_GLTF_3X3.T
    assert np.allclose(got, expected_gltf)


def test_applying_geometry_mds_literal_lps_matrix_to_ras_data_would_mirror_it() -> None:
    """The concrete, verified finding: an 'anterior' landmark (large +Y) at RAS x=-5 maps to
    glTF x=-0.005 under our matrix, and to x=+0.005 (sign-flipped) under GEOMETRY.md's literal
    LPS matrix applied to the same RAS point -- a left-right mirror."""
    point_ras_mm = np.array([-5.0, 10.0, -5.0])
    correct = (point_ras_mm @ RAS_TO_GLTF_3X3.T) * MM_TO_M
    wrong = (point_ras_mm @ _LPS_TO_GLTF_3X3.T) * MM_TO_M
    assert correct[0] == pytest.approx(-0.005)
    assert wrong[0] == pytest.approx(0.005)
    assert np.sign(correct[0]) != np.sign(wrong[0])


def test_centring_offset_uses_the_bounding_box_centre_not_the_vertex_mean() -> None:
    # three points whose MEAN differs from their bounding-box CENTRE
    verts = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [10.0, 0.0, 0.0]])
    offset = centring_offset_mm(verts)
    assert np.allclose(offset, [-5.0, 0.0, 0.0])  # bbox centre is 5, not the mean (6.67)


def test_to_gltf_space_centres_before_converting_axes_so_the_offset_is_in_mm() -> None:
    verts = np.array([[95.0, -55.0, 25.0], [105.0, -45.0, 35.0]])  # far from the origin, in mm
    result = to_gltf_space(verts)
    assert np.allclose(result.centring_offset_mm, [-100.0, 50.0, -30.0])
    # magnitude is millimetre-scale, not metre-scale
    assert np.abs(result.centring_offset_mm).max() > 1.0
    # and the exported vertices are centred at the origin, in METRES
    assert np.allclose(result.vertices_gltf_m.mean(axis=0), 0.0, atol=1e-9)
    assert np.abs(result.vertices_gltf_m).max() < 0.1  # a 10mm box -> well under 0.1m


def test_to_gltf_space_scales_millimetres_to_metres() -> None:
    verts = np.array([[0.0, 0.0, 0.0], [1000.0, 0.0, 0.0]])  # 1000mm = 1m apart
    result = to_gltf_space(verts)
    span = result.vertices_gltf_m.max(axis=0) - result.vertices_gltf_m.min(axis=0)
    assert np.allclose(span, [1.0, 0.0, 0.0])


def test_normals_are_rotated_but_not_translated_or_scaled() -> None:
    verts = np.array([[100.0, 100.0, 100.0], [110.0, 100.0, 100.0]])  # far from origin
    normals = np.array([[0.0, 1.0, 0.0], [0.0, -1.0, 0.0]])  # anterior / posterior facing
    result = to_gltf_space(verts, normals)
    assert result.normals_gltf is not None
    assert np.allclose(result.normals_gltf, [[0.0, 0.0, -1.0], [0.0, 0.0, 1.0]])
    lengths = np.linalg.norm(result.normals_gltf, axis=1)
    assert np.allclose(lengths, 1.0)  # rotation preserves unit length


def test_normals_are_none_when_not_provided() -> None:
    verts = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    result = to_gltf_space(verts)
    assert result.normals_gltf is None


def test_the_input_arrays_are_never_mutated() -> None:
    verts = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
    normals = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    verts_before, normals_before = verts.copy(), normals.copy()
    to_gltf_space(verts, normals)
    assert np.array_equal(verts, verts_before)
    assert np.array_equal(normals, normals_before)
