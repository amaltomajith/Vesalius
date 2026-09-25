"""S10's `postprocess_mesh` against small hand-built meshes: largest-component selection,
degenerate cleanup, the Taubin sign finding (see mesh_postprocess.py's module docstring),
decimation behaviour and determinism, and postconditions.
"""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

from pipeline.common.errors import MeshPostconditionError, MeshZeroFacesError
from pipeline.stages.s10_mesh_postprocess.mesh_postprocess import (
    check_postconditions,
    postprocess_mesh,
)

DEFAULT = {"taubin_lamb": 0.5, "taubin_nu": 0.53, "taubin_iterations": 12, "target_faces": 10_000}


def _run(vertices: np.ndarray, faces: np.ndarray, **over):  # type: ignore[no-untyped-def]
    kwargs = {**DEFAULT, **over}
    return postprocess_mesh(vertices, faces, **kwargs)


def _two_disjoint_pieces() -> tuple[np.ndarray, np.ndarray, int, int]:
    # a box (12 faces) always has the same face count regardless of size -- use different
    # subdivision levels so "largest" is unambiguous by FACE COUNT, not just physical extent
    big = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    small = trimesh.creation.icosphere(subdivisions=0, radius=2.0)
    small.apply_translation((100.0, 100.0, 100.0))  # far away, definitely disjoint
    verts = np.vstack([big.vertices, small.vertices])
    faces = np.vstack([big.faces, small.faces + len(big.vertices)])
    return verts, faces, len(big.faces), len(small.faces)


def test_largest_component_keeps_the_bigger_disjoint_piece() -> None:
    verts, faces, big_face_count, small_face_count = _two_disjoint_pieces()
    assert big_face_count > small_face_count
    r = _run(verts, faces, target_faces=10_000)  # no decimation at this scale
    assert r.stats["after_largest_component_faces"] == big_face_count
    # the result should not contain any vertex from the small, far-away box
    assert r.vertices.max() < 50.0


def test_a_single_component_mesh_is_unaffected_by_the_largest_component_step() -> None:
    box = trimesh.creation.box(extents=(10, 10, 10))
    r = _run(box.vertices, box.faces, target_faces=10_000)
    assert r.stats["after_largest_component_faces"] == len(box.faces)


def test_degenerate_and_duplicate_faces_are_removed() -> None:
    box = trimesh.creation.box(extents=(10, 10, 10))
    verts = box.vertices.copy()
    faces = box.faces.copy()
    # a zero-area (degenerate) face and an exact duplicate of an existing face
    degenerate = np.array([[0, 0, 0]])
    duplicate = faces[0:1]
    corrupted_faces = np.vstack([faces, degenerate, duplicate])
    r = _run(verts, corrupted_faces, target_faces=10_000)
    assert r.stats["after_degenerate_cleanup_faces"] == len(faces)


def test_taubin_with_the_corrected_sign_preserves_volume_far_better_than_the_geometry_md_sign() -> (
    None
):
    """GEOMETRY.md's literal `nu=-0.53` shrinks a mesh continuously under trimesh's actual
    `filter_taubin` (verified empirically -- see mesh_postprocess.py). This pins the corrected
    sign's behaviour down directly: after 12 iterations, volume must stay close to the original,
    and must clearly beat what the wrong sign would have done."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    rng = np.random.default_rng(0)
    noisy_verts = sphere.vertices + rng.normal(scale=0.3, size=sphere.vertices.shape)
    base_volume = trimesh.Trimesh(noisy_verts, sphere.faces, process=False).volume

    good = _run(noisy_verts, sphere.faces, taubin_nu=0.53, target_faces=100_000)
    bad = _run(noisy_verts, sphere.faces, taubin_nu=-0.53, target_faces=100_000)

    good_volume = trimesh.Trimesh(good.vertices, good.faces, process=False).volume
    bad_volume = trimesh.Trimesh(bad.vertices, bad.faces, process=False).volume

    assert abs(good_volume / base_volume - 1.0) < 0.02  # within 2%
    assert abs(bad_volume / base_volume - 1.0) > abs(good_volume / base_volume - 1.0)


def test_decimation_reduces_the_face_count_toward_the_target() -> None:
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    assert len(sphere.faces) > 1000
    r = _run(sphere.vertices, sphere.faces, target_faces=500)
    assert r.stats["decimation_applied"] is True
    assert r.stats["final_face_count"] <= 500
    assert r.stats["final_face_count"] > 0


def test_decimation_is_skipped_and_never_inflates_a_small_mesh() -> None:
    box = trimesh.creation.box(extents=(10, 10, 10))
    r = _run(box.vertices, box.faces, target_faces=10_000)  # way more than a box needs
    assert r.stats["decimation_applied"] is False
    assert r.stats["after_decimation_faces"] == r.stats["after_degenerate_cleanup_faces"]


def test_decimation_is_deterministic_across_repeated_runs() -> None:
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    a = _run(sphere.vertices, sphere.faces, target_faces=500)
    b = _run(sphere.vertices, sphere.faces, target_faces=500)
    assert np.array_equal(a.vertices, b.vertices)
    assert np.array_equal(a.faces, b.faces)


def test_normals_are_computed_and_match_the_vertex_count() -> None:
    box = trimesh.creation.box(extents=(10, 10, 10))
    r = _run(box.vertices, box.faces, target_faces=10_000)
    assert r.normals.shape == r.vertices.shape
    # unit-length (or very close) per-vertex normals
    lengths = np.linalg.norm(r.normals, axis=1)
    assert np.allclose(lengths, 1.0, atol=1e-3)


def test_fix_normals_makes_winding_consistent_even_from_a_badly_wound_input() -> None:
    """GEOMETRY.md: "decimation invalidates normals" -- pymeshlab's decimation was found to
    preserve consistent winding on a well-formed input, so this test manufactures the
    inconsistency directly (reversing ~half the input faces' winding) rather than relying on
    decimation to happen to produce it. Without the final `fix_normals()` step, this input
    comes out ~50% inward-facing (verified); with it, the output must be outward-consistent."""
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    faces = sphere.faces.copy()
    rng = np.random.default_rng(1)
    flip = rng.random(len(faces)) > 0.5
    faces[flip] = faces[flip][:, [0, 2, 1]]  # reverse winding on ~half the faces

    r = _run(sphere.vertices, faces, target_faces=10_000)
    out = trimesh.Trimesh(vertices=r.vertices, faces=r.faces, process=False)
    center = out.vertices.mean(axis=0)
    outward = np.einsum("ij,ij->i", out.face_normals, out.triangles_center - center)
    assert (outward > 0).mean() > 0.99


def test_an_all_degenerate_mesh_fails_closed() -> None:
    verts = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    faces = np.array([[0, 1, 2]])  # zero area: every vertex identical
    with pytest.raises(MeshZeroFacesError):
        _run(verts, faces, target_faces=10_000)


def test_an_empty_input_mesh_fails_closed() -> None:
    with pytest.raises(MeshZeroFacesError) as exc:
        _run(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), target_faces=10_000)
    assert exc.value.detail["step"] == "input"


def test_the_input_arrays_are_never_mutated() -> None:
    box = trimesh.creation.box(extents=(10, 10, 10))
    verts_before, faces_before = box.vertices.copy(), box.faces.copy()
    _run(box.vertices, box.faces, target_faces=10_000)
    assert np.array_equal(box.vertices, verts_before)
    assert np.array_equal(box.faces, faces_before)


def test_check_postconditions_catches_an_out_of_range_face_index() -> None:
    vertices = np.zeros((3, 3))
    normals = np.zeros((3, 3))
    ok_faces = np.array([[0, 1, 2]])
    check_postconditions(vertices, ok_faces, normals)  # does not raise

    bad_faces = np.array([[0, 1, 99]])
    with pytest.raises(MeshPostconditionError) as exc:
        check_postconditions(vertices, bad_faces, normals)
    assert exc.value.detail["reason"] == "face_index_out_of_range"


def test_check_postconditions_catches_a_normals_shape_mismatch() -> None:
    vertices = np.zeros((3, 3))
    faces = np.array([[0, 1, 2]])
    with pytest.raises(MeshPostconditionError) as exc:
        check_postconditions(vertices, faces, np.zeros((2, 3)))
    assert exc.value.detail["reason"] == "normals_shape_mismatch"


def test_check_postconditions_accepts_an_empty_face_array() -> None:
    check_postconditions(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 3)))
