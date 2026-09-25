"""S9's `extract_mesh` against small hand-built volumes: dilation widens the confinement region
measurably, zero faces fails closed, postconditions catch a broken conversion, and every failure
mode has its own reason code. Marching cubes' exact vertex/face count isn't hand-derivable for
anything but a trivial case, so assertions check shape/sign/magnitude, not literal counts (same
precedent as S7's phantom tests).
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.common.errors import (
    SurfaceAffineInvalidError,
    SurfaceGeometryMismatchError,
    SurfaceInvariantViolationError,
    SurfaceZeroFacesError,
)
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh

AIR = -1000.0
DEFAULT = {
    "air_value": AIR,
    "dilation_voxels": 2,
    "step_size": 1,
    "world_bbox_tolerance_mm": 5.0,
}


def _sphere(shape: tuple[int, int, int], radius: float, value: float) -> np.ndarray:
    v = np.full(shape, AIR, dtype=np.float32)
    center = np.array(shape) / 2.0
    k, j, i = np.indices(shape)
    dist = np.sqrt((k - center[0]) ** 2 + (j - center[1]) ** 2 + (i - center[2]) ** 2)
    v[dist <= radius] = value
    return v


def _run(
    volume: np.ndarray,
    mask: np.ndarray,
    affine: np.ndarray = np.eye(4),
    level: float = 300.0,
    **over,
):  # type: ignore[no-untyped-def]
    kwargs = {**DEFAULT, **over}
    return extract_mesh(volume, mask, affine, level=level, **kwargs)


def test_a_solid_sphere_produces_a_plausible_closed_surface() -> None:
    v = _sphere((30, 30, 30), radius=10.0, value=400.0)
    mask = v >= 300.0
    r = _run(v, mask)
    assert r.stats["vertex_count"] > 100
    assert r.stats["face_count"] > 100
    # every face triangle: 2 faces per vertex on average, roughly, for a closed manifold
    assert r.faces.shape[1] == 3
    assert r.vertices_ras.shape[1] == 3


def test_marching_cubes_radius_matches_the_analytic_sphere_within_tolerance() -> None:
    v = _sphere((40, 40, 40), radius=12.0, value=400.0)
    mask = v >= 300.0
    r = _run(v, mask)
    dist = np.linalg.norm(r.vertices_ras - 20.0, axis=1)  # centre at (20,20,20) in this affine
    assert np.median(dist) == pytest.approx(12.0, abs=0.75)


def test_dilation_widens_the_confinement_region_measurably() -> None:
    v = _sphere((30, 30, 30), radius=10.0, value=400.0)
    mask = v >= 300.0
    small = _run(v, mask, dilation_voxels=0)
    large = _run(v, mask, dilation_voxels=4)
    assert large.stats["confine_voxels"] > small.stats["confine_voxels"]


def test_a_mask_that_excludes_all_tissue_finds_no_surface() -> None:
    v = _sphere((20, 20, 20), radius=8.0, value=400.0)
    empty_mask = np.zeros_like(v, dtype=bool)
    with pytest.raises(SurfaceZeroFacesError) as exc:
        _run(v, empty_mask, dilation_voxels=0)
    assert exc.value.reason_code == "S9_ZERO_FACES"


def test_a_uniform_volume_below_level_finds_no_surface() -> None:
    v = np.full((10, 10, 10), -1000.0, dtype=np.float32)
    mask = np.ones_like(v, dtype=bool)
    with pytest.raises(SurfaceZeroFacesError):
        _run(v, mask)


def test_a_uniform_volume_above_level_also_finds_no_surface() -> None:
    v = np.full((10, 10, 10), 2000.0, dtype=np.float32)
    mask = np.ones_like(v, dtype=bool)
    with pytest.raises(SurfaceZeroFacesError):
        _run(v, mask)


def test_a_shape_mismatch_between_volume_and_mask_is_refused() -> None:
    v = _sphere((10, 10, 10), radius=3.0, value=400.0)
    mask = np.ones((10, 10, 9), dtype=bool)
    with pytest.raises(SurfaceGeometryMismatchError) as exc:
        _run(v, mask)
    assert exc.value.reason_code == "S9_GEOMETRY_MISMATCH"


@pytest.mark.parametrize(
    "affine",
    [np.diag([1.0, 2.0, 1.0, 1.0]), np.diag([2.0, 2.0, 2.0, 1.0])],
    ids=["anisotropic", "wrong-spacing"],
)
def test_a_non_isotropic_or_mis_scaled_affine_is_refused(affine: np.ndarray) -> None:
    v = _sphere((10, 10, 10), radius=3.0, value=400.0)
    mask = v >= 300.0
    with pytest.raises(SurfaceAffineInvalidError) as exc:
        _run(v, mask, affine=affine)
    assert exc.value.reason_code == "S9_AFFINE_INVALID"


def test_the_input_volume_and_mask_are_never_mutated() -> None:
    v = _sphere((20, 20, 20), radius=7.0, value=400.0)
    mask = v >= 300.0
    v_before, mask_before = v.copy(), mask.copy()
    _run(v, mask)
    assert np.array_equal(v, v_before)
    assert np.array_equal(mask, mask_before)


def test_a_corrupted_marching_cubes_output_is_caught_by_the_world_bbox_postcondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates the regression class the postcondition exists to catch -- vertices nowhere
    near the volume's own physical extent -- while leaving `index_vertices_to_world` itself
    real, so the bounding box is computed correctly and the mismatch is genuine."""
    import pipeline.stages.s9_surface_extraction.mesh_extract as mod

    def fake_marching_cubes(masked_volume, *, level, step_size, allow_degenerate):  # type: ignore[no-untyped-def]
        verts = np.array([[1.0e6, 1.0e6, 1.0e6], [1.0e6, 1.0e6, 1.0e6 + 1]])
        faces = np.array([[0, 0, 1]])
        return verts, faces, None, None

    monkeypatch.setattr(mod.measure, "marching_cubes", fake_marching_cubes)

    v = _sphere((10, 10, 10), radius=3.0, value=400.0)
    mask = v >= 300.0
    with pytest.raises(SurfaceInvariantViolationError) as exc:
        _run(v, mask)
    assert exc.value.reason_code == "S9_INVARIANT_VIOLATION"
    assert exc.value.detail["reason"] == "vertex_outside_world_bbox"


def test_a_successful_call_that_still_returns_zero_faces_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """marching_cubes normally raises on no crossing (covered above); this exercises the
    separate, redundant-but-real guard for the case where it returns successfully with an
    empty face array anyway."""
    import pipeline.stages.s9_surface_extraction.mesh_extract as mod

    def fake_marching_cubes(masked_volume, *, level, step_size, allow_degenerate):  # type: ignore[no-untyped-def]
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), None, None

    monkeypatch.setattr(mod.measure, "marching_cubes", fake_marching_cubes)
    v = _sphere((10, 10, 10), radius=3.0, value=400.0)
    mask = v >= 300.0
    with pytest.raises(SurfaceZeroFacesError) as exc:
        _run(v, mask)
    assert exc.value.detail["reason"] == "zero_faces"


def test_a_face_index_out_of_range_is_caught_by_its_own_postcondition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulates a corrupted marching_cubes output: valid-looking vertices (inside the world
    bbox, so the OTHER postcondition doesn't fire first) but a face referencing a vertex index
    that doesn't exist."""
    import pipeline.stages.s9_surface_extraction.mesh_extract as mod

    def fake_marching_cubes(masked_volume, *, level, step_size, allow_degenerate):  # type: ignore[no-untyped-def]
        verts = np.array([[5.0, 5.0, 5.0], [5.0, 5.0, 6.0], [5.0, 6.0, 5.0]])
        faces = np.array([[0, 1, 99]])  # index 99 does not exist
        return verts, faces, None, None

    monkeypatch.setattr(mod.measure, "marching_cubes", fake_marching_cubes)
    v = _sphere((10, 10, 10), radius=3.0, value=400.0)
    mask = v >= 300.0
    with pytest.raises(SurfaceInvariantViolationError) as exc:
        _run(v, mask)
    assert exc.value.detail["reason"] == "face_index_out_of_range"


def test_zero_faces_reason_distinguishes_no_crossing_from_an_empty_mask() -> None:
    v = _sphere((20, 20, 20), radius=7.0, value=400.0)
    with pytest.raises(SurfaceZeroFacesError) as exc:
        _run(v, np.zeros_like(v, dtype=bool), dilation_voxels=0)
    assert exc.value.detail["reason"] == "no_level_crossing"
