"""S10 against the analytic head phantom's bone mesh (shared with S7-S9's tests): the
post-processed mesh stays a recognisable bone shell after smoothing and decimation, and its
vertex cloud is still the same physical object across orientations.

Same caveat as S7-S9's phantom tests: shows the algorithm behaves as designed and is
orientation-independent, not clinical mesh fidelity.
"""

from __future__ import annotations

import numpy as np
import pytest

from pipeline.stages.s8_segment import params as s8_params
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.stages.s9_surface_extraction import params as s9_params
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh
from pipeline.stages.s10_mesh_postprocess import params as s10_params
from pipeline.stages.s10_mesh_postprocess.mesh_postprocess import postprocess_mesh
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import phantom

VARIANTS = sorted(hp.ROTATIONS)
BASE = "lps_native_default"


def _bone_mesh(name: str):  # type: ignore[no-untyped-def]
    ph = phantom(name)
    lo, hi = s8_params.preset_bounds("bone")
    mask = segment(
        ph.volume,
        lo=lo,
        hi=hi,
        closing_iterations=s8_params.CLOSING_ITERATIONS,
        opening_iterations=s8_params.OPENING_ITERATIONS,
    ).mask
    raw = extract_mesh(
        ph.volume,
        mask,
        ph.grid.affine_ras,
        level=s9_params.level_for("bone"),
        air_value=-1000.0,
        dilation_voxels=s9_params.DILATION_VOXELS,
        step_size=s9_params.STEP_SIZE,
        world_bbox_tolerance_mm=s9_params.WORLD_BBOX_TOLERANCE_MM,
    )
    return postprocess_mesh(
        raw.vertices_ras,
        raw.faces,
        taubin_lamb=s10_params.TAUBIN_LAMB,
        taubin_nu=s10_params.TAUBIN_NU,
        taubin_iterations=s10_params.TAUBIN_ITERATIONS,
        target_faces=50_000,
    )


@pytest.mark.parametrize("name", VARIANTS)
def test_the_optimised_bone_mesh_still_clusters_near_the_skull_shell_radius(name: str) -> None:
    r = _bone_mesh(name)
    center = np.array([0.0, 0.0, 0.0])
    dist = np.linalg.norm(r.vertices - center, axis=1)
    # smoothing + decimation widen the band slightly relative to S9's raw-mesh test
    assert np.percentile(dist, 5) > 50.0
    assert np.percentile(dist, 95) < 112.0


def test_decimation_reaches_the_requested_budget_on_a_real_phantom_mesh() -> None:
    r = _bone_mesh(BASE)
    assert r.stats["input_faces"] > 400_000  # S9's raw mesh, per the earlier smoke run
    assert r.stats["decimation_applied"] is True
    assert r.stats["final_face_count"] <= 50_000


def test_the_optimised_mesh_is_the_same_physical_object_on_every_orientation() -> None:
    counts = {n: _bone_mesh(n).stats["final_face_count"] for n in VARIANTS}
    base = counts[VARIANTS[0]]
    for n, c in counts.items():
        assert abs(c - base) / base < 0.05, (n, counts)
