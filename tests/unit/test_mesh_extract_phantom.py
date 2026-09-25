"""S9 against the analytic head phantom (shared with S7/S8's tests): orientation-independence
and the project's thesis restated at the mesh level -- a defaced phantom's `skin_surface` mesh
has no vertex anywhere near the nose tip; the un-defaced one does.

Same caveat as S7/S8's phantom tests: this shows the algorithm behaves as designed and is
orientation-independent, not that it produces clinically faithful mesh geometry on real anatomy.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.stages.s8_segment import params as s8_params
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.stages.s9_surface_extraction import params as s9_params
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import AIR_VALUE, defaced, phantom

VARIANTS = sorted(hp.ROTATIONS)
BASE = "lps_native_default"


def _bone_mesh(volume: np.ndarray, affine: np.ndarray):  # type: ignore[no-untyped-def]
    lo, hi = s8_params.preset_bounds("bone")
    mask = segment(
        volume,
        lo=lo,
        hi=hi,
        closing_iterations=s8_params.CLOSING_ITERATIONS,
        opening_iterations=s8_params.OPENING_ITERATIONS,
    ).mask
    return extract_mesh(
        volume,
        mask,
        affine,
        level=s9_params.level_for("bone"),
        air_value=AIR_VALUE,
        dilation_voxels=s9_params.DILATION_VOXELS,
        step_size=s9_params.STEP_SIZE,
        world_bbox_tolerance_mm=s9_params.WORLD_BBOX_TOLERANCE_MM,
    )


def _skin_surface_mesh(volume: np.ndarray, affine: np.ndarray):  # type: ignore[no-untyped-def]
    lo, hi = s8_params.preset_bounds("skin_surface")
    mask = segment(
        volume,
        lo=lo,
        hi=hi,
        closing_iterations=s8_params.CLOSING_ITERATIONS,
        opening_iterations=s8_params.OPENING_ITERATIONS,
    ).mask
    return extract_mesh(
        volume,
        mask,
        affine,
        level=s9_params.level_for("skin_surface"),
        air_value=AIR_VALUE,
        dilation_voxels=s9_params.DILATION_VOXELS,
        step_size=s9_params.STEP_SIZE,
        world_bbox_tolerance_mm=s9_params.WORLD_BBOX_TOLERANCE_MM,
    )


@pytest.mark.parametrize("name", VARIANTS)
def test_the_bone_mesh_clusters_near_the_analytic_skull_shell_radius(name: str) -> None:
    ph = phantom(name)
    r = _bone_mesh(ph.volume, ph.grid.affine_ras)
    center = np.array([0.0, 0.0, 0.0])  # HEAD_C in head_phantom.py
    dist = np.linalg.norm(r.vertices_ras - center, axis=1)
    # skull semi-axes are 70/90/100 (head_phantom.HEAD_R), shell ~5-12mm inside the skin --
    # every vertex should fall in a generous band around that
    assert np.percentile(dist, 5) > 55.0
    assert np.percentile(dist, 95) < 100.0


def test_the_bone_mesh_is_the_same_physical_object_on_every_orientation() -> None:
    counts = {
        n: _bone_mesh(phantom(n).volume, phantom(n).grid.affine_ras).stats["vertex_count"]
        for n in VARIANTS
    }
    base = counts[VARIANTS[0]]
    for n, c in counts.items():
        assert abs(c - base) / base < 0.05, (n, counts)


@pytest.mark.parametrize("name", VARIANTS)
def test_thesis_a_defaced_phantoms_skin_surface_mesh_has_no_vertex_near_the_nose(name: str) -> None:
    """CLAUDE.md's thesis, restated at the mesh level: a surface mesh from a head CT
    reconstructs a face; defacing must make that impossible, not just move some voxels."""
    ph = phantom(name)
    res = defaced(name)
    nose_tip_world = np.array([0.0, 123.0, -10.0])  # head_phantom.NOSE centre, front face

    before = _skin_surface_mesh(ph.volume, ph.grid.affine_ras)
    after = _skin_surface_mesh(res.defaced, ph.grid.affine_ras)

    dist_before = np.linalg.norm(before.vertices_ras - nose_tip_world, axis=1)
    dist_after = np.linalg.norm(after.vertices_ras - nose_tip_world, axis=1)

    assert dist_before.min() < 20.0  # the un-defaced mesh reaches the nose
    assert dist_after.min() > 25.0  # the defaced mesh does not
