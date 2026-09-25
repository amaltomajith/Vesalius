"""S9's real `_execute` against a hand-built `GatedVolume` + a hand-built S8 mask (no DB, no
MinIO, no S7/S8 machinery -- `GatedVolume` is sealed, same forging pattern S7/S8's stub tests
use). DB-backed behaviour (transform_runs, quarantine rows, gate/mask-lookup skips, idempotent
reruns) is covered by tests/integration/test_s9_surface_extraction_pipeline.py.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any
from uuid import uuid4

import numpy as np
import pytest

from pipeline.common.errors import (
    SurfaceGeometryMismatchError,
    SurfaceInputTamperedError,
    SurfaceModalityUnsupportedError,
    SurfaceOutputMismatchError,
    SurfaceUnreadableInputError,
)
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s7_deface import gate as gate_mod
from pipeline.stages.s7_deface.nifti_io import load_volume
from pipeline.stages.s8_segment import nifti_io as mask_io
from pipeline.stages.s8_segment import params as s8_params
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.stages.s9_surface_extraction import mesh_io, service
from tests.fixtures.deface_runs import S6Stub, make_s6_stub, nifti_bytes, phantom, phantom_nifti

BASE = "lps_native_default"


def _gated(
    stub: S6Stub,
    *,
    required: bool = True,
    method: str = "geometric_v1",
    reason: str = "body_part=HEAD",
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
        _block={"required": required, "method": method, "reason": reason},
        _seal=gate_mod._TOKEN,
    )


def _mask_bytes(volume_bytes: bytes, preset: str = "bone") -> bytes:
    loaded = load_volume(volume_bytes)
    lo, hi = s8_params.preset_bounds(preset)
    mask = segment(
        loaded.array,
        lo=lo,
        hi=hi,
        closing_iterations=s8_params.CLOSING_ITERATIONS,
        opening_iterations=s8_params.OPENING_ITERATIONS,
    ).mask
    return mask_io.write_mask(loaded.image, mask)


def _mask_ref(mask_bytes: bytes) -> service._MaskRef:
    return service._MaskRef(
        object_key="whatever/mask_bone.nii.gz", sha256=sha256_of_bytes(mask_bytes)
    )


def _head_stub(**kw: Any) -> S6Stub:
    ph = phantom(BASE)
    return make_s6_stub(phantom_nifti(BASE), ph.volume.shape, ph.grid.affine_ras, **kw)  # type: ignore[arg-type]


def _run(
    gated: gate_mod.GatedVolume,
    mask_bytes: bytes,
    *,
    preset: str = "bone",
    skip_deface: bool = False,
) -> service._Produced:
    return service._execute(
        gated=gated,
        mask_ref=_mask_ref(mask_bytes),
        mask_bytes=mask_bytes,
        preset=preset,
        skip_deface=skip_deface,
        prefix="pfx",
    )


@cache
def _bone_run() -> tuple[S6Stub, bytes, service._Produced]:
    stub = _head_stub()
    mb = _mask_bytes(stub.volume_bytes)
    return stub, mb, _run(_gated(stub), mb)


def test_a_mesh_is_written_to_a_run_scoped_key() -> None:
    stub, mb, produced = _bone_run()
    assert produced.mesh_key == "pfx/surface_bone.ply"
    assert produced.report_key == "pfx/surface_extraction_report.json"
    assert produced.mesh_bytes


def test_the_mesh_round_trips_through_ply_within_float32_precision() -> None:
    """PLY's binary vertex encoding is float32 (see mesh_io.py) -- not bit-exact, but close.
    `_execute` already verified this internally (it would have raised otherwise); this
    re-reads independently and checks the same tolerance directly.
    """
    stub, mb, produced = _bone_run()
    verts, faces = mesh_io.load_mesh(produced.mesh_bytes)
    assert verts.shape[0] == produced.stats["vertex_count"]
    assert faces.shape[0] == produced.stats["face_count"]
    assert verts.dtype == np.float64  # widened back on read, even though PLY stored float32
    assert faces.dtype == np.int64


def test_the_report_records_the_deface_block_algorithm_and_stats() -> None:
    stub, mb, produced = _bone_run()
    r = json.loads(produced.report_bytes)
    assert r["deface"] == {
        "required": True,
        "applied": True,
        "method": "geometric_v1",
        "reason": "body_part=HEAD",
    }
    assert r["skip_deface_requested"] is False
    assert r["algorithm"]["preset"] == "bone"
    assert r["algorithm"]["level"] == 300.0
    assert r["algorithm"]["dilation_voxels"] == 2
    assert r["algorithm"]["step_size"] == 1
    assert r["input"]["volume"]["sha256"] == sha256_of_bytes(stub.volume_bytes)
    assert r["input"]["mask"]["sha256"] == sha256_of_bytes(mb)
    assert r["output"]["mesh"]["sha256"] == sha256_of_bytes(produced.mesh_bytes)
    assert r["stats"]["vertex_count"] == produced.stats["vertex_count"]
    assert len(r["residual_risks"]) >= 4
    assert produced.outcome.output_hashes == [
        sha256_of_bytes(produced.mesh_bytes),
        sha256_of_bytes(produced.report_bytes),
    ]


def test_skip_deface_true_is_recorded_but_changes_nothing_about_the_result() -> None:
    stub, mb, base = _bone_run()
    flagged = _run(_gated(stub), mb, skip_deface=True)
    assert json.loads(flagged.report_bytes)["skip_deface_requested"] is True
    assert flagged.stats["vertex_count"] == base.stats["vertex_count"]
    assert flagged.stats["face_count"] == base.stats["face_count"]


def test_a_report_never_contains_a_run_id_or_a_timestamp() -> None:
    _, _, produced = _bone_run()
    text = produced.report_bytes.decode()
    assert "produced_by_run" not in text
    assert "created_at" not in text
    assert "started_at" not in text


def test_a_rebuild_of_the_same_inputs_is_byte_identical() -> None:
    stub, mb, first = _bone_run()
    second = _run(_gated(stub), mb)
    assert second.mesh_bytes == first.mesh_bytes
    assert second.report_bytes == first.report_bytes


def test_a_non_ct_or_hu_invalid_gated_volume_is_refused() -> None:
    stub = _head_stub(modality="MR")
    mb = _mask_bytes(stub.volume_bytes)
    with pytest.raises(SurfaceModalityUnsupportedError):
        _run(_gated(stub), mb)
    stub2 = _head_stub(hu_valid=False)
    mb2 = _mask_bytes(stub2.volume_bytes)
    with pytest.raises(SurfaceModalityUnsupportedError):
        _run(_gated(stub2), mb2)


def test_an_unreadable_volume_or_mask_is_refused() -> None:
    stub, mb, _ = _bone_run()
    with pytest.raises(SurfaceUnreadableInputError):
        _run(_gated(stub), b"not a nifti")
    bad_gated = gate_mod.GatedVolume(
        series_id=stub.series.id,
        volume_bytes=b"not a nifti",
        volume_sha256=sha256_of_bytes(b"not a nifti"),
        volume_key=stub.s6.volume.object_key,
        meta_bytes=stub.meta_bytes,
        meta_sha256=sha256_of_bytes(stub.meta_bytes),
        s7_run_id=uuid4(),
        source=gate_mod.SOURCE_DEFACED,
        _block={"required": True, "method": "geometric_v1", "reason": "body_part=HEAD"},
        _seal=gate_mod._TOKEN,
    )
    with pytest.raises(SurfaceUnreadableInputError):
        _run(bad_gated, mb)


def test_a_mask_with_a_different_geometry_than_the_volume_is_refused() -> None:
    stub, _, _ = _bone_run()
    other_ph = phantom("ras_native")
    other_mask = _mask_bytes(nifti_bytes(other_ph.volume, other_ph.grid.affine_ras))
    with pytest.raises(SurfaceGeometryMismatchError):
        _run(_gated(stub), other_mask)


def test_a_write_path_that_alters_the_mesh_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`write_mesh` silently writing something other than what `extract_mesh` computed must be
    caught by the re-read comparison -- `_execute` compares the RE-READ bytes against the
    ORIGINAL in-memory vertices it computed, not against whatever the writer produced."""
    real = mesh_io.write_mesh

    def corrupting(verts: np.ndarray, faces: np.ndarray) -> bytes:
        return real(verts + 1.0, faces)

    stub, mb, _ = _bone_run()
    monkeypatch.setattr(service.mesh_io, "write_mesh", corrupting)
    with pytest.raises(SurfaceOutputMismatchError):
        _run(_gated(stub), mb)


def test_a_tampered_mask_object_is_refused() -> None:
    from pipeline.common.config import Settings
    from tests.fixtures.volume_asserts import StubSilverClient

    client = StubSilverClient({"masks/bone.nii.gz": b"corrupted-bytes"})
    ref = service._MaskRef(object_key="masks/bone.nii.gz", sha256="z" * 64)
    with pytest.raises(SurfaceInputTamperedError):
        service._read_verified(client, Settings(), ref)  # type: ignore[arg-type]


def test_an_unreadable_mask_object_is_refused() -> None:
    from pipeline.common.config import Settings
    from tests.fixtures.volume_asserts import StubSilverClient

    client = StubSilverClient({})
    ref = service._MaskRef(object_key="missing.nii.gz", sha256="z" * 64)
    with pytest.raises(SurfaceUnreadableInputError):
        service._read_verified(client, Settings(), ref)  # type: ignore[arg-type]


def test_an_unsupported_preset_is_a_value_error_before_any_db_or_gate_work() -> None:
    with pytest.raises(ValueError, match="unsupported preset"):
        service.extract_surface(None, None, None, uuid4(), "vessel_ce")  # type: ignore[arg-type]


def test_skip_deface_is_never_a_real_bypass() -> None:
    """TESTING.md Proof 2's `extract_surface(series, skip_deface=True)` must still raise
    `DefacingGateError` on an undefaced head. Statically: the code between opening the gate and
    handling its refusal must never reference `skip_deface` -- if it ever does, the flag has
    become a real bypass and this test must fail. (The runtime half of Proof 2 -- the actual
    raise -- needs a live gate and lives in the integration tier.)
    """
    import inspect

    src = inspect.getsource(service.extract_surface)
    gate_call = src.index("open_defacing_gate(")
    block_start = src.rindex("try:", 0, gate_call)
    block_end = src.index("mask_ref = latest_s8_mask", gate_call)
    gate_block = src[block_start:block_end]
    assert "open_defacing_gate(" in gate_block  # sanity: found the right block
    assert "skip_deface" not in gate_block
