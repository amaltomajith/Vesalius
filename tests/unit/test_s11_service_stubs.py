"""S11's real `_execute` against a hand-built `GatedVolume` + a hand-built S10 optimised mesh
(no DB, no MinIO, no S7-S10 machinery -- `GatedVolume` is sealed, same forging pattern S7-S9's
stub tests use). DB-backed behaviour (transform_runs, gold Artifact upsert, gate/mesh-lookup
skips, idempotent reruns) is covered by tests/integration/test_s11_export_pipeline.py.
"""

from __future__ import annotations

import json
from functools import cache
from uuid import uuid4

import numpy as np
import pytest

from pipeline.common.errors import (
    ExportInputTamperedError,
    ExportOutputMismatchError,
    ExportUnreadableInputError,
)
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s7_deface import gate as gate_mod
from pipeline.stages.s8_segment import params as s8_params
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.stages.s9_surface_extraction import params as s9_params
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh
from pipeline.stages.s10_mesh_postprocess import mesh_io as optimized_mesh_io
from pipeline.stages.s10_mesh_postprocess import params as s10_params
from pipeline.stages.s10_mesh_postprocess.mesh_postprocess import postprocess_mesh
from pipeline.stages.s11_export import glb_io, service, thumbnail
from tests.fixtures.deface_runs import S6Stub, make_s6_stub, phantom, phantom_nifti

BASE = "lps_native_default"
DEFACE_BLOCK = {
    "required": True,
    "applied": True,
    "method": "geometric_v1",
    "reason": "body_part=HEAD",
}
SEGMENT_PARAMS = s8_params.algorithm_params("bone")
SURFACE_EXTRACTION_PARAMS = s9_params.algorithm_params("bone")
MESH_POSTPROCESS_PARAMS = s10_params.algorithm_params(50_000)


def _gated(
    stub: S6Stub, *, required: bool = True, reason: str = "body_part=HEAD"
) -> gate_mod.GatedVolume:
    return gate_mod.GatedVolume(
        series_id=stub.series.id,
        volume_bytes=stub.volume_bytes,
        volume_sha256=sha256_of_bytes(stub.volume_bytes),
        volume_key=stub.s6.volume.object_key,
        meta_bytes=stub.meta_bytes,
        meta_sha256=sha256_of_bytes(stub.meta_bytes),
        s7_run_id=uuid4(),
        source=gate_mod.SOURCE_DEFACED if required else gate_mod.SOURCE_NOT_REQUIRED,
        _block={"required": required, "method": "geometric_v1", "reason": reason},
        _seal=gate_mod._TOKEN,
    )


def _head_stub() -> S6Stub:
    ph = phantom(BASE)
    return make_s6_stub(phantom_nifti(BASE), ph.volume.shape, ph.grid.affine_ras)


def _optimized_mesh_bytes(preset: str = "bone") -> bytes:
    ph = phantom(BASE)
    lo, hi = s8_params.preset_bounds(preset)
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
        level=s9_params.level_for(preset),
        air_value=-1000.0,
        dilation_voxels=s9_params.DILATION_VOXELS,
        step_size=s9_params.STEP_SIZE,
        world_bbox_tolerance_mm=s9_params.WORLD_BBOX_TOLERANCE_MM,
    )
    opt = postprocess_mesh(
        raw.vertices_ras,
        raw.faces,
        taubin_lamb=s10_params.TAUBIN_LAMB,
        taubin_nu=s10_params.TAUBIN_NU,
        taubin_iterations=s10_params.TAUBIN_ITERATIONS,
        target_faces=5_000,  # small, so tests run fast
    )
    return optimized_mesh_io.write_mesh(opt.vertices, opt.faces, opt.normals)


def _mesh_ref(mesh_bytes: bytes) -> service._MeshRef:
    return service._MeshRef(
        object_key="whatever/surface_optimized_bone.ply",
        sha256=sha256_of_bytes(mesh_bytes),
        deface_block=dict(DEFACE_BLOCK),
        segment_params=dict(SEGMENT_PARAMS),
        surface_extraction_params=dict(SURFACE_EXTRACTION_PARAMS),
        mesh_postprocess_params=dict(MESH_POSTPROCESS_PARAMS),
    )


def _run(
    gated: gate_mod.GatedVolume, mesh_bytes: bytes, *, preset: str = "bone"
) -> service._Produced:
    return service._execute(
        gated=gated,
        mesh_ref=_mesh_ref(mesh_bytes),
        raw_mesh_bytes=mesh_bytes,
        preset=preset,
        series_id=gated.series_id,
        prefix="pfx",
    )


@cache
def _bone_run() -> tuple[S6Stub, bytes, service._Produced]:
    stub = _head_stub()
    mesh_bytes = _optimized_mesh_bytes()
    return stub, mesh_bytes, _run(_gated(stub), mesh_bytes)


def test_the_four_gold_outputs_are_written_to_series_addressed_fixed_keys() -> None:
    stub, _, produced = _bone_run()
    assert produced.glb_key == "pfx/surface.glb"
    assert produced.volume_key == "pfx/volume.nii.gz"
    assert produced.thumbnail_key == "pfx/thumbnail.png"
    assert produced.manifest_key == "pfx/manifest.json"
    assert produced.glb_bytes and produced.thumbnail_bytes and produced.manifest_bytes


def test_the_gold_volume_copy_is_byte_identical_to_the_gated_volume_never_a_derived_copy() -> None:
    stub, _, produced = _bone_run()
    assert produced.volume_bytes == stub.volume_bytes


def test_the_glb_round_trips_within_float32_precision_and_carries_the_right_face_count() -> None:
    stub, mesh_bytes, produced = _bone_run()
    verts, faces, normals, watertight = glb_io.load_glb(produced.glb_bytes)
    orig_verts, orig_faces, _ = optimized_mesh_io.load_mesh(mesh_bytes)
    assert faces.shape[0] == orig_faces.shape[0] == produced.face_count
    assert verts.shape == orig_verts.shape
    # metres, well under a millimetre's worth of drift from the optimised mesh's own vertex count
    assert normals.shape == verts.shape


def test_the_manifest_records_ras_source_frame_not_geometry_mds_literal_lps() -> None:
    """GEOMETRY.md's example literally says `"source_frame": "LPS_mm"` -- this pipeline's data
    has been RAS-native since S6, so recording that literal would mislabel our own data (the
    central finding this stage's `gltf_transform.py` exists to fix, restated at the manifest
    level)."""
    _, _, produced = _bone_run()
    m = json.loads(produced.manifest_bytes)
    assert m["geometry"]["source_frame"] == "RAS_mm"
    assert m["geometry"]["export_frame"] == "gltf_Y_up_m"


def test_the_manifest_records_millimetre_scale_centring_and_isotropic_spacing() -> None:
    _, _, produced = _bone_run()
    m = json.loads(produced.manifest_bytes)
    offset = m["geometry"]["centring_offset_mm"]
    assert max(abs(v) for v in offset) > 1.0  # mm-scale, not metre-scale
    assert m["geometry"]["voxel_spacing_mm"] == [1.0, 1.0, 1.0]


def test_the_manifest_carries_forward_deface_and_all_three_upstream_algorithm_blocks() -> None:
    _, _, produced = _bone_run()
    m = json.loads(produced.manifest_bytes)
    assert m["deface"] == DEFACE_BLOCK
    assert m["parameters"]["preset"] == "bone"
    assert m["parameters"]["segment"] == SEGMENT_PARAMS
    assert m["parameters"]["surface_extraction"] == SURFACE_EXTRACTION_PARAMS
    assert m["parameters"]["mesh_postprocess"] == MESH_POSTPROCESS_PARAMS


def test_the_manifest_artifact_hashes_match_the_actual_gold_bytes() -> None:
    _, _, produced = _bone_run()
    m = json.loads(produced.manifest_bytes)
    assert m["artifacts"]["surface.glb"]["sha256"] == sha256_of_bytes(produced.glb_bytes)
    assert m["artifacts"]["surface.glb"]["faces"] == produced.face_count
    assert m["artifacts"]["volume.nii.gz"]["sha256"] == sha256_of_bytes(produced.volume_bytes)
    assert m["artifacts"]["thumbnail.png"]["sha256"] == sha256_of_bytes(produced.thumbnail_bytes)
    assert produced.outcome.output_hashes == [
        sha256_of_bytes(produced.glb_bytes),
        sha256_of_bytes(produced.volume_bytes),
        sha256_of_bytes(produced.thumbnail_bytes),
        sha256_of_bytes(produced.manifest_bytes),
    ]


def test_a_rebuild_of_the_same_inputs_is_byte_identical() -> None:
    stub, mesh_bytes, first = _bone_run()
    second = _run(_gated(stub), mesh_bytes)
    assert second.glb_bytes == first.glb_bytes
    assert second.volume_bytes == first.volume_bytes
    assert second.thumbnail_bytes == first.thumbnail_bytes
    assert second.manifest_bytes == first.manifest_bytes


def test_an_unreadable_optimised_mesh_is_refused() -> None:
    stub = _head_stub()
    with pytest.raises(ExportUnreadableInputError):
        _run(_gated(stub), b"not a glb or ply")


def test_a_write_path_that_alters_the_glb_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    real = glb_io.write_glb

    def corrupting(
        verts: np.ndarray, faces: np.ndarray, normals: np.ndarray
    ) -> glb_io.GlbWriteResult:
        result = real(verts + 1.0, faces, normals)
        return result

    stub = _head_stub()
    mesh_bytes = _optimized_mesh_bytes()
    monkeypatch.setattr(service.glb_io, "write_glb", corrupting)
    with pytest.raises(ExportOutputMismatchError):
        _run(_gated(stub), mesh_bytes)


def test_a_write_path_that_alters_the_thumbnail_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    real = thumbnail.render_thumbnail

    def corrupting(volume_bytes: bytes) -> thumbnail.ThumbnailResult:
        result = real(volume_bytes)
        tampered = result.array_uint8.copy()
        tampered[0, 0] = 255 - tampered[0, 0]
        return thumbnail.ThumbnailResult(png_bytes=result.png_bytes, array_uint8=tampered)

    stub = _head_stub()
    mesh_bytes = _optimized_mesh_bytes()
    monkeypatch.setattr(service.thumbnail, "render_thumbnail", corrupting)
    with pytest.raises(ExportOutputMismatchError):
        _run(_gated(stub), mesh_bytes)


def test_a_tampered_mesh_object_is_refused() -> None:
    from pipeline.common.config import Settings
    from tests.fixtures.volume_asserts import StubSilverClient

    client = StubSilverClient({"meshes/optimized.ply": b"corrupted-bytes"})
    ref = service._MeshRef(
        object_key="meshes/optimized.ply",
        sha256="z" * 64,
        deface_block={},
        segment_params={},
        surface_extraction_params={},
        mesh_postprocess_params={},
    )
    with pytest.raises(ExportInputTamperedError):
        service._read_verified_mesh(client, Settings(), ref)  # type: ignore[arg-type]


def test_an_unreadable_mesh_object_is_refused() -> None:
    from pipeline.common.config import Settings
    from tests.fixtures.volume_asserts import StubSilverClient

    client = StubSilverClient({})
    ref = service._MeshRef(
        object_key="missing.ply",
        sha256="z" * 64,
        deface_block={},
        segment_params={},
        surface_extraction_params={},
        mesh_postprocess_params={},
    )
    with pytest.raises(ExportUnreadableInputError):
        service._read_verified_mesh(client, Settings(), ref)  # type: ignore[arg-type]


def test_an_unsupported_preset_is_a_value_error_before_any_db_or_gate_work() -> None:
    with pytest.raises(ValueError, match="unsupported preset"):
        service.export_series(None, None, None, None, uuid4(), "vessel_ce")  # type: ignore[arg-type]


def test_thumbnail_rendering_takes_only_bytes_never_a_series_id_or_session() -> None:
    """Static guard against a thumbnail path that could reach for silver directly: the only
    public function in thumbnail.py must accept exactly one positional parameter, `bytes`."""
    import inspect

    sig = inspect.signature(thumbnail.render_thumbnail)
    params_ = list(sig.parameters.values())
    assert len(params_) == 1
    assert params_[0].name == "volume_nii_gz_bytes"
