"""S10's real `_execute` against a hand-built S9 mesh reference (no DB, no MinIO, no S7-S9
machinery). DB-backed behaviour (transform_runs, quarantine rows, no-mesh skips, idempotent
reruns) is covered by tests/integration/test_s10_mesh_postprocess_pipeline.py.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any
from uuid import uuid4

import numpy as np
import pytest
import trimesh

from pipeline.common.errors import MeshOutputMismatchError, MeshUnreadableInputError
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s8_segment import params as s8_params
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.stages.s9_surface_extraction import mesh_io as raw_mesh_io
from pipeline.stages.s9_surface_extraction import params as s9_params
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh
from pipeline.stages.s10_mesh_postprocess import mesh_io, service
from tests.fixtures.deface_runs import phantom

BASE = "lps_native_default"
FIXED_SERIES_ID = uuid4()
DEFACE_BLOCK = {
    "required": True,
    "applied": True,
    "method": "geometric_v1",
    "reason": "body_part=HEAD",
}


def _raw_mesh_bytes(preset: str = "bone") -> bytes:
    ph = phantom(BASE)
    lo, hi = s8_params.preset_bounds(preset)
    mask = segment(
        ph.volume,
        lo=lo,
        hi=hi,
        closing_iterations=s8_params.CLOSING_ITERATIONS,
        opening_iterations=s8_params.OPENING_ITERATIONS,
    ).mask
    res = extract_mesh(
        ph.volume,
        mask,
        ph.grid.affine_ras,
        level=s9_params.level_for(preset),
        air_value=-1000.0,
        dilation_voxels=s9_params.DILATION_VOXELS,
        step_size=s9_params.STEP_SIZE,
        world_bbox_tolerance_mm=s9_params.WORLD_BBOX_TOLERANCE_MM,
    )
    return raw_mesh_io.write_mesh(res.vertices_ras, res.faces)


def _mesh_ref(raw_bytes: bytes, deface_block: dict[str, Any] | None = None) -> service._MeshRef:
    return service._MeshRef(
        object_key="whatever/surface_bone.ply",
        sha256=sha256_of_bytes(raw_bytes),
        deface_block=deface_block if deface_block is not None else dict(DEFACE_BLOCK),
    )


def _run(
    raw_bytes: bytes,
    *,
    preset: str = "bone",
    target_faces: int = 50_000,
    deface_block=None,  # type: ignore[no-untyped-def]
) -> service._Produced:
    return service._execute(
        series_id=FIXED_SERIES_ID,
        mesh_ref=_mesh_ref(raw_bytes, deface_block),
        raw_mesh_bytes=raw_bytes,
        preset=preset,
        target_faces=target_faces,
        prefix="pfx",
    )


@cache
def _bone_run() -> tuple[bytes, service._Produced]:
    raw = _raw_mesh_bytes()
    return raw, _run(raw)


def test_a_mesh_is_written_to_a_run_scoped_key() -> None:
    raw, produced = _bone_run()
    assert produced.mesh_key == "pfx/surface_optimized_bone.ply"
    assert produced.report_key == "pfx/mesh_postprocess_report.json"
    assert produced.mesh_bytes


def test_the_optimized_mesh_carries_normals_and_fewer_faces_than_the_raw_mesh() -> None:
    raw, produced = _bone_run()
    verts, faces, normals = mesh_io.load_mesh(produced.mesh_bytes)
    assert normals.shape == verts.shape
    raw_verts, raw_faces = raw_mesh_io.load_mesh(raw)
    assert len(faces) < len(raw_faces)
    assert len(faces) <= 50_000


def test_the_report_records_the_deface_block_target_and_stats() -> None:
    raw, produced = _bone_run()
    r = json.loads(produced.report_bytes)
    assert r["deface"] == DEFACE_BLOCK
    assert r["algorithm"]["target_faces"] == 50_000
    assert r["algorithm"]["taubin_nu"] == 0.53  # the corrected sign, never the ref's literal
    assert r["input"]["mesh"]["sha256"] == sha256_of_bytes(raw)
    assert r["output"]["mesh"]["sha256"] == sha256_of_bytes(produced.mesh_bytes)
    assert r["stats"]["final_face_count"] == produced.stats["final_face_count"]
    assert len(r["residual_risks"]) >= 3
    assert produced.outcome.output_hashes == [
        sha256_of_bytes(produced.mesh_bytes),
        sha256_of_bytes(produced.report_bytes),
    ]


def test_the_deface_block_is_carried_forward_verbatim_not_rederived() -> None:
    raw = _raw_mesh_bytes()
    custom = {"required": False, "applied": False, "method": "none", "reason": "body_part=CHEST"}
    produced = _run(raw, deface_block=custom)
    assert json.loads(produced.report_bytes)["deface"] == custom


def test_a_report_never_contains_a_run_id_or_a_timestamp() -> None:
    _, produced = _bone_run()
    text = produced.report_bytes.decode()
    assert "produced_by_run" not in text
    assert "created_at" not in text
    assert "started_at" not in text


def test_a_rebuild_of_the_same_inputs_is_byte_identical() -> None:
    raw, first = _bone_run()
    second = _run(raw)
    assert second.mesh_bytes == first.mesh_bytes
    assert second.report_bytes == first.report_bytes


def test_an_unreadable_raw_mesh_is_refused() -> None:
    with pytest.raises(MeshUnreadableInputError):
        _run(b"not a ply")


def test_a_write_path_that_alters_the_mesh_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    real = mesh_io.write_mesh

    def corrupting(verts: np.ndarray, faces: np.ndarray, normals: np.ndarray) -> bytes:
        return real(verts + 1.0, faces, normals)

    raw = _raw_mesh_bytes()
    monkeypatch.setattr(service.mesh_io, "write_mesh", corrupting)
    with pytest.raises(MeshOutputMismatchError):
        _run(raw)


def test_a_write_path_that_drops_normals_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    real = mesh_io.write_mesh

    def flattening(verts: np.ndarray, faces: np.ndarray, normals: np.ndarray) -> bytes:
        return real(verts, faces, np.zeros_like(normals))

    raw = _raw_mesh_bytes()
    monkeypatch.setattr(service.mesh_io, "write_mesh", flattening)
    with pytest.raises(MeshOutputMismatchError):
        _run(raw)


def test_decimation_is_skipped_when_the_raw_mesh_is_already_small() -> None:
    box = trimesh.creation.box(extents=(10, 10, 10))
    raw = raw_mesh_io.write_mesh(box.vertices, box.faces)
    produced = _run(raw, target_faces=10_000)
    assert json.loads(produced.report_bytes)["stats"]["decimation_applied"] is False


def test_an_unsupported_preset_is_a_value_error_before_any_db_work() -> None:
    with pytest.raises(ValueError, match="unsupported preset"):
        service.postprocess_mesh_series(None, None, None, uuid4(), "vessel_ce")  # type: ignore[arg-type]
