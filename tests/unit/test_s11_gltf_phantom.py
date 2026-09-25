"""S11's `gltf_transform` against the full S8->S9->S10 mesh chain on the analytic head phantom
(shared with S7-S10's tests): the nose landmark -- the phantom's one unambiguous anterior
feature -- must land at a NEGATIVE glTF Z (Anterior -> -Forward) after export.

The phantom is bilaterally symmetric in x by construction (see head_phantom.py: every feature
is centred at x=0), so this test cannot exercise the LEFT-RIGHT mirror check -- that is covered
directly, with an asymmetric synthetic point, in test_gltf_transform.py. What this test adds is
the thing a pure-function unit test cannot: proof that the real S9/S10 output, run through
`to_gltf_space` unmodified, actually places the nose in front (-Z), not behind (+Z) -- which is
exactly what GEOMETRY.md's literal LPS-input matrix would do if applied to this pipeline's
RAS-native vertices (its z-row reads RAS y directly with no sign flip, so the nose's large
+y would land at a large +Z instead).
"""

from __future__ import annotations

import numpy as np

from pipeline.stages.s8_segment import params as s8_params
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.stages.s9_surface_extraction import params as s9_params
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh
from pipeline.stages.s10_mesh_postprocess import params as s10_params
from pipeline.stages.s10_mesh_postprocess.mesh_postprocess import postprocess_mesh
from pipeline.stages.s11_export.gltf_transform import to_gltf_space
from tests.fixtures.deface_runs import AIR_VALUE, phantom

BASE = "lps_native_default"
NOSE_TIP_RAS_MM = np.array([0.0, 123.0, -10.0])  # head_phantom.NOSE centre, front of face

# GEOMETRY.md's literal matrix, for LPS input -- reading RAS y straight into glTF z, unflipped.
_LPS_TO_GLTF_3X3 = np.array(
    [
        [-1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, 1.0, 0.0],
    ]
)


def _optimised_skin_surface_mesh():  # type: ignore[no-untyped-def]
    ph = phantom(BASE)
    lo, hi = s8_params.preset_bounds("skin_surface")
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
        level=s9_params.level_for("skin_surface"),
        air_value=AIR_VALUE,
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


def test_the_nose_lands_in_front_not_behind_after_the_full_s9_s10_s11_chain() -> None:
    r = _optimised_skin_surface_mesh()
    dist = np.linalg.norm(r.vertices - NOSE_TIP_RAS_MM, axis=1)
    nose_idx = int(np.argmin(dist))
    assert dist[nose_idx] < 20.0  # the mesh does reach the nose (sanity check on the fixture)

    result = to_gltf_space(r.vertices)
    nose_gltf = result.vertices_gltf_m[nose_idx]

    assert nose_gltf[2] < -0.05  # Anterior -> -Forward: the nose is well in front of centre

    # what GEOMETRY.md's literal LPS matrix would have done to the SAME real mesh: reading RAS
    # y straight into glTF z puts the nose well BEHIND centre instead -- the concrete bug this
    # stage's RAS_TO_GLTF derivation avoids, demonstrated on real S9/S10 output, not a synthetic
    # point.
    wrong_z = (r.vertices[nose_idx] @ _LPS_TO_GLTF_3X3.T)[2] * 1e-3
    assert wrong_z > 0.05
    assert np.sign(nose_gltf[2]) != np.sign(wrong_z)
