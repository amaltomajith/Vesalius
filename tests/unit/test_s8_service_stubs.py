"""S8's real `_execute` against a hand-built `GatedVolume` (no DB, no MinIO, no S7 machinery --
`GatedVolume` is sealed, so tests construct one directly via the gate's own token, the same
pattern `test_defacing_gate.py` uses to prove forgery is otherwise refused). DB-backed behaviour
(transform_runs, quarantine rows, gate-refusal skips, idempotent reruns) is covered by
tests/integration/test_s8_segment_pipeline.py.
"""

from __future__ import annotations

import json
import os
import tempfile
from functools import cache
from typing import Any
from uuid import uuid4

import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813

from pipeline.common.errors import (
    EmptyMaskError,
    MaskTooLargeError,
    SegmentGeometryMismatchError,
    SegmentModalityUnsupportedError,
    SegmentOutputHeaderMismatchError,
    SegmentUnreadableInputError,
)
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s7_deface import gate as gate_mod
from pipeline.stages.s8_segment import nifti_io as mask_io
from pipeline.stages.s8_segment import params, service
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


def _head_stub(**kw: Any) -> S6Stub:
    ph = phantom(BASE)
    return make_s6_stub(phantom_nifti(BASE), ph.volume.shape, ph.grid.affine_ras, **kw)  # type: ignore[arg-type]


def _run(gated: gate_mod.GatedVolume, preset: str = "bone") -> service._Produced:
    return service._execute(gated=gated, preset=preset, prefix="pfx")


@cache
def _bone_run() -> tuple[S6Stub, service._Produced]:
    stub = _head_stub()
    return stub, _run(_gated(stub))


def test_a_bone_mask_is_written_to_a_run_scoped_key() -> None:
    stub, produced = _bone_run()
    assert produced.mask_key == "pfx/mask_bone.nii.gz"
    assert produced.report_key == "pfx/segmentation_report.json"
    assert produced.mask_bytes


def test_the_mask_bytes_are_the_algorithms_output_with_the_sources_geometry() -> None:
    from pipeline.stages.s7_deface.nifti_io import load_volume

    stub, produced = _bone_run()
    src = load_volume(stub.volume_bytes)
    mask = mask_io.load_mask(produced.mask_bytes)
    assert mask.shape == src.array.shape
    assert mask.dtype == bool
    from pipeline.stages.s8_segment.segmentation import segment

    expected = segment(
        src.array,
        lo=300,
        hi=3000,
        closing_iterations=params.CLOSING_ITERATIONS,
        opening_iterations=params.OPENING_ITERATIONS,
    )
    assert np.array_equal(mask, expected.mask)


def test_the_report_records_the_deface_block_thresholds_and_stats() -> None:
    stub, produced = _bone_run()
    r = json.loads(produced.report_bytes)
    assert r["deface"] == {
        "required": True,
        "applied": True,
        "method": "geometric_v1",
        "reason": "body_part=HEAD",
    }
    assert r["algorithm"]["preset"] == "bone"
    assert r["algorithm"]["thresholds"] == {"min": 300.0, "max": 3000.0}
    assert r["input"]["volume"]["sha256"] == sha256_of_bytes(stub.volume_bytes)
    assert r["output"]["mask"]["sha256"] == sha256_of_bytes(produced.mask_bytes)
    assert r["stats"]["final_voxels"] == produced.stats["final_voxels"]
    assert r["header_check"] == {
        "dims_match": True,
        "pixdim_close": True,
        "codes_match": True,
        "geometry_close": True,
        "sform_equal": True,
    }
    assert len(r["residual_risks"]) >= 4
    assert produced.outcome.output_hashes == [
        sha256_of_bytes(produced.mask_bytes),
        sha256_of_bytes(produced.report_bytes),
    ]


def test_a_report_never_contains_a_run_id_or_a_timestamp() -> None:
    _, produced = _bone_run()
    text = produced.report_bytes.decode()
    assert "produced_by_run" not in text
    assert "created_at" not in text
    assert "started_at" not in text


def test_a_rebuild_of_the_same_inputs_is_byte_identical() -> None:
    stub, first = _bone_run()
    second = _run(_gated(stub))
    assert second.mask_bytes == first.mask_bytes
    assert second.report_bytes == first.report_bytes


def test_a_non_ct_or_hu_invalid_gated_volume_is_refused() -> None:
    stub = _head_stub(modality="MR")
    with pytest.raises(SegmentModalityUnsupportedError):
        _run(_gated(stub))
    stub2 = _head_stub(hu_valid=False)
    with pytest.raises(SegmentModalityUnsupportedError):
        _run(_gated(stub2))


def test_an_unreadable_volume_is_refused() -> None:
    stub = _head_stub()
    bad = gate_mod.GatedVolume(
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
    with pytest.raises(SegmentUnreadableInputError):
        _run(bad)


def test_an_unreadable_meta_is_refused() -> None:
    stub = _head_stub()
    bad = gate_mod.GatedVolume(
        series_id=stub.series.id,
        volume_bytes=stub.volume_bytes,
        volume_sha256=sha256_of_bytes(stub.volume_bytes),
        volume_key=stub.s6.volume.object_key,
        meta_bytes=b"not json",
        meta_sha256=sha256_of_bytes(b"not json"),
        s7_run_id=uuid4(),
        source=gate_mod.SOURCE_DEFACED,
        _block={"required": True, "method": "geometric_v1", "reason": "body_part=HEAD"},
        _seal=gate_mod._TOKEN,
    )
    with pytest.raises(SegmentUnreadableInputError):
        _run(bad)


def test_a_meta_shape_or_affine_that_disagrees_with_the_volume_header_is_refused() -> None:
    ph = phantom(BASE)
    shifted = ph.grid.affine_ras.copy()
    shifted[0, 3] += 5.0
    stub = make_s6_stub(
        phantom_nifti(BASE), ph.volume.shape, ph.grid.affine_ras, meta_affine=shifted
    )  # type: ignore[arg-type]
    with pytest.raises(SegmentGeometryMismatchError):
        _run(_gated(stub))


def test_an_empty_mask_is_refused() -> None:
    v = np.full((6, 6, 6), -1000.0, dtype=np.float32)
    stub = make_s6_stub(nifti_bytes(v, np.eye(4)), v.shape, np.eye(4))
    with pytest.raises(EmptyMaskError) as exc:
        _run(_gated(stub))
    assert exc.value.detail["preset"] == "bone"
    assert exc.value.detail["final_voxels"] == 0


def test_a_mask_covering_more_than_90_percent_is_refused() -> None:
    # entirely bone-range; large enough that the border trap's edge shell (see segmentation.py)
    # is a small fraction of the total, so coverage still clears 90%
    v = np.full((200, 200, 200), 400.0, dtype=np.float32)
    stub = make_s6_stub(nifti_bytes(v, np.eye(4)), v.shape, np.eye(4))
    with pytest.raises(MaskTooLargeError) as exc:
        _run(_gated(stub))
    assert exc.value.detail["coverage_fraction"] > params.MAX_MASK_FRACTION


def test_a_mask_written_with_a_shifted_origin_is_a_header_mismatch_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Companion to the write-path test below: this corrupts the GEOMETRY instead of the
    voxels, so it must be caught by `mask_io.compare_geometry`, not the re-read check."""

    def shifted(source: Any, mask: np.ndarray) -> bytes:
        image = sitk.GetImageFromArray(np.ascontiguousarray(mask, dtype=np.uint8))
        image.CopyInformation(source)
        origin = image.GetOrigin()
        image.SetOrigin((origin[0] + 5.0, *origin[1:]))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "mask.nii.gz")
            sitk.WriteImage(image, path)
            with open(path, "rb") as fh:
                return fh.read()

    monkeypatch.setattr(service.mask_io, "write_mask", shifted)
    stub = _head_stub()
    with pytest.raises(SegmentOutputHeaderMismatchError) as exc:
        _run(_gated(stub))
    assert exc.value.detail.get("sform_equal") is False


def test_a_write_path_that_alters_the_mask_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    real = mask_io.write_mask

    def corrupting(source: Any, mask: np.ndarray) -> bytes:
        broken = mask.copy()
        idx = np.argwhere(~broken)[0] if (~broken).any() else np.array([0, 0, 0])
        broken[tuple(idx)] = not broken[tuple(idx)]
        return real(source, broken)

    monkeypatch.setattr(service.mask_io, "write_mask", corrupting)
    stub = _head_stub()
    with pytest.raises(SegmentOutputHeaderMismatchError) as exc:
        _run(_gated(stub))
    assert exc.value.detail.get("failure") == "write_path_altered_voxels"


def test_a_not_required_gated_volume_is_segmented_too_and_the_deface_block_says_so() -> None:
    """S8 segments whatever the gate handed it -- a not-required (S6) volume is a legitimate
    volume to segment, just not a defaced one; the report's `deface` block records that."""
    stub = _head_stub()
    gated = _gated(stub, required=False, method="none", reason="body_part=CHEST")
    produced = _run(gated)
    r = json.loads(produced.report_bytes)
    assert r["deface"] == {
        "required": False,
        "applied": False,
        "method": "none",
        "reason": "body_part=CHEST",
    }
    assert produced.stats["final_voxels"] > 0


def test_an_unsupported_preset_is_a_value_error_before_any_db_or_gate_work() -> None:
    with pytest.raises(ValueError, match="unsupported preset"):
        service.segment_series(None, None, None, uuid4(), "vessel_ce")  # type: ignore[arg-type]
