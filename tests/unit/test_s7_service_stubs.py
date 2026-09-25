"""S7's real `_execute` and `gather_header_body_parts` against in-memory stand-ins for MinIO and the
catalog (no infrastructure): what gets written, what is refused, and that the source silver
bytes are never touched. DB-backed behaviour (transform_runs, quarantine rows, idempotent reruns)
is covered by tests/integration/test_s7_deface_pipeline.py.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

import numpy as np
import pytest

from pipeline.common.config import Settings
from pipeline.common.errors import (
    DefaceAffineMismatchError,
    DefaceEfficacyCheckFailedError,
    DefaceInputTamperedError,
    DefaceModalityUnsupportedError,
    DefaceOutputHeaderMismatchError,
    DefaceUnreadableInputError,
    NoHeadFoundError,
)
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s7_deface import nifti_io, service
from pipeline.stages.s7_deface.decision import DefaceDecision, decide_deface
from pipeline.stages.s7_deface.verify import VerifyReport
from tests.fixtures.deface_runs import (
    S6Stub,
    defaced,
    make_s6_stub,
    nifti_bytes,
    phantom,
    phantom_nifti,
)
from tests.fixtures.dicom_factory import build_series

BASE = "lps_native_default"
SETTINGS = Settings()


def _decision(
    body_part: str = "HEAD", flag: bool = True, headers: list[str | None] | None = None
) -> DefaceDecision:
    return decide_deface(
        study_body_part=body_part,
        study_body_part_source="tag",
        study_deface_required_flag=flag,
        series_header_body_parts=headers or [],
    )


def _head_stub(**kw: Any) -> S6Stub:
    ph = phantom(BASE)
    return make_s6_stub(phantom_nifti(BASE), ph.volume.shape, ph.grid.affine_ras, **kw)  # type: ignore[arg-type]


def _run(stub: S6Stub, decision: DefaceDecision) -> service._Produced:
    return service._execute(
        silver_client=stub.client,  # type: ignore[arg-type]
        settings=SETTINGS,
        series=stub.series,
        s6=stub.s6,
        decision=decision,
        prefix="pfx",
    )


@cache
def _head_run() -> tuple[S6Stub, service._Produced]:
    stub = _head_stub()
    return stub, _run(stub, _decision())


def test_a_required_head_is_defaced_to_a_run_scoped_key_and_the_source_is_untouched() -> None:
    stub, produced = _head_run()
    assert produced.volume_bytes is not None
    assert produced.volume_key == "pfx/volume_defaced.nii.gz"
    assert produced.volume_key != stub.s6.volume.object_key
    assert stub.client.objects[stub.s6.volume.object_key] == stub.volume_bytes  # untouched
    assert produced.report_key == "pfx/deface_report.json"


def test_the_defaced_bytes_are_the_algorithms_output_with_an_identical_header() -> None:
    stub, produced = _head_run()
    assert produced.volume_bytes is not None
    out = nifti_io.load_volume(produced.volume_bytes)
    assert nifti_io.header_bytes(produced.volume_bytes) == nifti_io.header_bytes(stub.volume_bytes)
    assert np.array_equal(out.array, defaced(BASE).defaced)


def test_the_report_records_the_decision_hashes_and_change_evidence() -> None:
    stub, produced = _head_run()
    assert produced.volume_bytes is not None
    r = json.loads(produced.report_bytes)
    assert r["applied"] is True
    assert r["decision"]["required"] is True
    assert r["decision"]["reason"] == "body_part=HEAD"
    assert r["algorithm"]["method"] == "geometric_v1"
    assert r["input"]["volume"]["sha256"] == stub.s6.volume.sha256
    assert r["input"]["meta"]["sha256"] == stub.s6.meta.sha256
    assert r["output"]["volume_defaced"]["sha256"] == sha256_of_bytes(produced.volume_bytes)
    assert r["change"]["bone_voxels_changed"] == 0
    assert r["change"]["outside_region_changed"] == 0
    assert r["change"]["changed_voxels"] == int(defaced(BASE).changed_mask.sum())
    assert r["change"]["removal_value"] == -1000.0
    assert r["efficacy"]["passed"] is True
    assert r["header_check"] == {
        "header_bytes_exact": True,
        "non_geometry_identical": True,
        "geometry_close": True,
        "sform_equal": True,
    }
    assert len(r["residual_risks"]) >= 5
    assert produced.outcome.output_hashes == [
        sha256_of_bytes(produced.volume_bytes),
        sha256_of_bytes(produced.report_bytes),
    ]


def test_a_rebuild_of_the_same_inputs_is_byte_identical() -> None:
    stub, first = _head_run()
    second = _run(stub, _decision())
    assert second.volume_bytes == first.volume_bytes
    assert second.report_bytes == first.report_bytes


def test_a_report_never_contains_a_run_id_or_a_timestamp() -> None:
    _, produced = _head_run()
    text = produced.report_bytes.decode()
    assert "produced_by_run" not in text and "created_at" not in text and "started_at" not in text


def test_not_required_writes_only_an_explained_report_and_reads_no_volume() -> None:
    arr = np.full((6, 6, 6), -1000.0, dtype=np.float32)
    aff = np.eye(4)
    stub = make_s6_stub(nifti_bytes(arr, aff), (6, 6, 6), aff)
    produced = _run(stub, _decision("CHEST", flag=False))
    assert produced.volume_bytes is None and produced.volume_key is None
    r = json.loads(produced.report_bytes)
    assert r["applied"] is False
    assert r["decision"]["required"] is False
    assert r["decision"]["reason"] == "body_part=CHEST"
    assert r["algorithm"] is None
    assert r["output"]["volume_defaced"] is None
    assert produced.outcome.output_hashes == [sha256_of_bytes(produced.report_bytes)]
    assert set(stub.client.objects) == {stub.s6.volume.object_key, stub.s6.meta.object_key}


@pytest.mark.parametrize(
    "kwargs",
    [{"modality": "MR"}, {"modality": "CT", "hu_valid": False}],
    ids=["mr", "ct-without-hu-semantics"],
)
def test_a_required_series_without_valid_hu_is_refused_not_given_a_weaker_algorithm(
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(DefaceModalityUnsupportedError):
        _run(_head_stub(**kwargs), _decision())


def test_an_mr_series_that_needs_no_defacing_is_not_refused() -> None:
    arr = np.zeros((6, 6, 6), dtype=np.float32)
    stub = make_s6_stub(nifti_bytes(arr, np.eye(4)), (6, 6, 6), np.eye(4), modality="MR")
    assert _run(stub, _decision("CHEST", flag=False)).volume_bytes is None


def test_a_silver_volume_that_no_longer_matches_the_catalog_hash_is_refused() -> None:
    stub = _head_stub()
    stub.client.objects[stub.s6.volume.object_key] = stub.volume_bytes + b"x"
    with pytest.raises(DefaceInputTamperedError):
        _run(stub, _decision())


def test_a_tampered_meta_is_refused_even_when_no_defacing_is_needed() -> None:
    stub = _head_stub()
    stub.client.objects[stub.s6.meta.object_key] = stub.meta_bytes + b" "
    with pytest.raises(DefaceInputTamperedError):
        _run(stub, _decision("CHEST", flag=False))


def test_a_missing_or_undecodable_object_is_an_unreadable_input_quarantine() -> None:
    stub = _head_stub()
    del stub.client.objects[stub.s6.volume.object_key]
    with pytest.raises(DefaceUnreadableInputError):
        _run(stub, _decision())
    junk = b"this is not a nifti"
    ph = phantom(BASE)
    stub2 = make_s6_stub(junk, ph.volume.shape, ph.grid.affine_ras)  # type: ignore[arg-type]
    with pytest.raises(DefaceUnreadableInputError):
        _run(stub2, _decision())


def test_a_meta_affine_that_disagrees_with_the_volume_header_is_refused() -> None:
    ph = phantom(BASE)
    shifted = ph.grid.affine_ras.copy()
    shifted[0, 3] += 5.0
    stub = make_s6_stub(
        phantom_nifti(BASE), ph.volume.shape, ph.grid.affine_ras, meta_affine=shifted
    )  # type: ignore[arg-type]
    with pytest.raises(DefaceAffineMismatchError):
        _run(stub, _decision())


def test_a_meta_shape_that_disagrees_with_the_volume_is_refused() -> None:
    ph = phantom(BASE)
    wrong = (ph.volume.shape[0] + 1, ph.volume.shape[1], ph.volume.shape[2])
    stub = make_s6_stub(phantom_nifti(BASE), wrong, ph.grid.affine_ras)
    with pytest.raises(DefaceAffineMismatchError):
        _run(stub, _decision())


def test_a_required_series_containing_no_head_is_refused() -> None:
    v = np.full((160, 200, 320), -1000.0, dtype=np.float32)
    v[5:155, 5:195, 5:315] = 40.0
    v[40:120, 40:160, 60:260] = 1000.0
    aff = np.eye(4)
    stub = make_s6_stub(nifti_bytes(v, aff), v.shape, aff)  # type: ignore[arg-type]
    with pytest.raises(NoHeadFoundError):
        _run(stub, _decision())


def test_a_defaced_header_that_differs_from_the_source_is_a_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = nifti_io.write_like

    def flipped(source: Any, array: np.ndarray) -> bytes:
        image = nifti_io.sitk.GetImageFromArray(array)
        image.CopyInformation(source)
        image.SetOrigin((source.GetOrigin()[0] + 1.0, *source.GetOrigin()[1:]))
        return real(image, array)

    monkeypatch.setattr(service.nifti_io, "write_like", flipped)
    with pytest.raises(DefaceOutputHeaderMismatchError):
        _run(_head_stub(), _decision())


def test_a_write_path_that_alters_voxels_is_caught_on_the_persisted_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = nifti_io.write_like

    def corrupting(source: Any, array: np.ndarray) -> bytes:
        broken = array.copy()
        broken.flat[broken.size // 2] += 1.0
        return real(source, broken)

    monkeypatch.setattr(service.nifti_io, "write_like", corrupting)
    with pytest.raises(DefaceEfficacyCheckFailedError) as exc:
        _run(_head_stub(), _decision())
    assert exc.value.detail["failures"] == ["write_path_altered_voxels"]


def test_a_failing_independent_verifier_blocks_the_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        service,
        "verify_defacing",
        lambda *a, **k: VerifyReport(False, ["anterior_retreat_too_small"], {}),
    )
    with pytest.raises(DefaceEfficacyCheckFailedError) as exc:
        _run(_head_stub(), _decision())
    assert exc.value.detail["failures"] == ["anterior_retreat_too_small"]


# ---- header evidence -------------------------------------------------------------------------


def _stub_with_instances(body_parts: list[str | None]) -> S6Stub:
    blobs = build_series(
        count=len(body_parts),
        overrides={i: {"body_part": bp} for i, bp in enumerate(body_parts)},
        pixel_data="valid",
    )
    import io

    import pydicom

    uids = [
        str(pydicom.dcmread(io.BytesIO(b), stop_before_pixels=True).SOPInstanceUID) for b in blobs
    ]
    ph_arr = np.zeros((4, 4, 4), dtype=np.float32)
    stub = make_s6_stub(nifti_bytes(ph_arr, np.eye(4)), (4, 4, 4), np.eye(4), pseudo_sop_uids=uids)
    for uid, blob in zip(uids, blobs, strict=True):
        stub.client.objects[f"{stub.series.id}/instances/{uid}.dcm"] = blob
    return stub


def _headers(stub: S6Stub) -> list[str | None]:
    return service.gather_header_body_parts(
        stub.client,
        SETTINGS,
        stub.series.id,
        stub.s6.meta,  # type: ignore[arg-type]
    )


def test_header_evidence_reads_the_first_and_last_instance() -> None:
    assert _headers(_stub_with_instances(["CHEST", "HEAD", "ABDOMEN"])) == ["CHEST", "ABDOMEN"]


def test_a_header_without_body_part_is_absent_evidence_not_an_error() -> None:
    assert _headers(_stub_with_instances([None, None])) == [None, None]


def test_unreadable_evidence_is_a_sentinel_that_forces_defacing() -> None:
    stub = _stub_with_instances(["CHEST", "CHEST"])
    del stub.client.objects[
        f"{stub.series.id}/instances/{stub.meta['quality']['slice_order_pseudo_sop_uids'][0]}.dcm"
    ]
    heads = _headers(stub)
    assert service.UNREADABLE_HEADER in heads
    assert _decision("CHEST", flag=False, headers=heads).required is True
    del stub.client.objects[stub.s6.meta.object_key]
    assert _headers(stub) == [service.UNREADABLE_HEADER]
