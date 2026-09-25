"""S7 and the defacing gate against live Postgres + MinIO.

Two sources of series: (1) the REAL S1->S6 pipeline for a chest CT (small, fast), and (2) a
synthetic head phantom whose S6-shaped catalog rows and silver objects are seeded directly --
pushing 200+ DICOM slices through S1-S5 would be slow and adds nothing to what S7 does.

Assertions are scoped to the ids each test creates; the session rolls back at the end (MinIO
objects are keyed by fresh series ids so orphans are harmless).
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import pydicom
import pytest
from sqlalchemy import select

from pipeline.catalog.models import Artifact, Quarantine, Series, Study, TransformRun
from pipeline.common.config import get_settings
from pipeline.common.errors import DefacingGateError
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s6_volume_assembly.service import assemble_volume_series
from pipeline.stages.s7_deface import nifti_io
from pipeline.stages.s7_deface.gate import open_defacing_gate
from pipeline.stages.s7_deface.service import deface_batch, deface_series
from pipeline.storage.object_store import get_bytes, put_bytes
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.deface_runs import make_s6_stub, nifti_bytes, phantom, phantom_nifti
from tests.fixtures.dicom_factory import build_clean_ct_series, build_series
from tests.fixtures.shared import integration

BASE = "lps_native_default"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _add_s6(session, silver, settings, series_id: UUID, volume_bytes, meta_bytes, *, later=0):  # type: ignore[no-untyped-def]
    """Seed one successful S6 run + its two artifacts (S6 overwrites these keys in place)."""
    run_id = uuid4()
    vkey, mkey = f"{series_id}/volume.nii.gz", f"{series_id}/meta.json"
    put_bytes(silver, settings.minio_silver_bucket, vkey, volume_bytes, "application/gzip")
    put_bytes(silver, settings.minio_silver_bucket, mkey, meta_bytes, "application/json")
    session.add(
        TransformRun(
            id=run_id,
            stage="S6",
            stage_version="1.0.0",
            idempotency_key=uuid4().hex,
            subject_type="series",
            subject_id=series_id,
            parameters={},
            input_hashes=[],
            output_hashes=[],
            status="success",
            started_at=datetime.now(UTC) + timedelta(seconds=later),
        )
    )
    session.flush()
    for kind, fmt, key, data in (
        ("volume_nifti", "nifti-1-gzip", vkey, volume_bytes),
        ("volume_meta", "json", mkey, meta_bytes),
    ):
        session.add(
            Artifact(
                id=uuid4(),
                series_id=series_id,
                kind=kind,
                format=fmt,
                zone="silver",
                object_key=key,
                sha256=_sha(data),
                bytes=len(data),
                produced_by_run=run_id,
            )
        )
    session.flush()


def _seed_series(  # type: ignore[no-untyped-def]
    session,
    silver,
    settings,
    *,
    volume_bytes,
    shape,
    affine,
    body_part="HEAD",
    flag=True,
    source="tag",
    modality="CT",
    quality="pass",
    with_s6=True,
    header_body_parts=None,
):
    study = Study(
        id=uuid4(),
        pseudo_study_uid=f"2.25.{uuid4().int}",
        pseudo_patient_id="P",
        modality=modality,
        body_part=body_part,
        body_part_source=source,
        deface_required=flag,
    )
    session.add(study)
    session.flush()
    series = Series(
        id=uuid4(),
        study_id=study.id,
        pseudo_series_uid=f"2.25.{uuid4().int}",
        modality=modality,
        instance_count=0,
        quality_status=quality,
    )
    session.add(series)
    session.flush()
    if with_s6:
        uids: list[str] = []
        if header_body_parts:
            blobs = build_series(
                count=len(header_body_parts),
                overrides={i: {"body_part": bp} for i, bp in enumerate(header_body_parts)},
                pixel_data="valid",
            )
            for blob in blobs:
                uid = str(pydicom.dcmread(io.BytesIO(blob), stop_before_pixels=True).SOPInstanceUID)
                uids.append(uid)
                put_bytes(
                    silver,
                    settings.minio_silver_bucket,
                    f"{series.id}/instances/{uid}.dcm",
                    blob,
                    "application/dicom",
                )
        stub = make_s6_stub(
            volume_bytes,
            shape,
            affine,
            modality=modality,
            series_id=series.id,
            pseudo_sop_uids=uids,
        )
        _add_s6(session, silver, settings, series.id, volume_bytes, stub.meta_bytes)
    return series.id


def _seed_head(session, silver, settings, **kw):  # type: ignore[no-untyped-def]
    ph = phantom(BASE)
    return _seed_series(
        session,
        silver,
        settings,
        volume_bytes=phantom_nifti(BASE),
        shape=ph.volume.shape,
        affine=ph.grid.affine_ras,
        **kw,
    )


def _s7_runs(session, series_id: UUID) -> list[TransformRun]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S7", TransformRun.subject_id == series_id
            )
        ).scalars()
    )


def _artifacts(session, series_id: UUID, kind: str) -> list[Artifact]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(Artifact).where(Artifact.series_id == series_id, Artifact.kind == kind)
        ).scalars()
    )


def _gate_code(session, silver, settings, series_id: UUID) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(DefacingGateError) as exc:
        open_defacing_gate(session, silver, settings, series_id)
    return exc.value.reason_code


@integration
def test_a_head_is_defaced_recorded_and_the_gate_returns_only_the_defaced_volume(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    s6_volume = _artifacts(pipeline_session, sid, "volume_nifti")[0]
    s6_meta = _artifacts(pipeline_session, sid, "volume_meta")[0]

    result = deface_series(pipeline_session, silver_minio_client, settings, sid)

    assert not result.skipped and not result.quarantined
    assert result.required is True and result.applied is True
    assert result.method == "geometric_v1" and result.reason == "body_part=HEAD"

    (run,) = _s7_runs(pipeline_session, sid)
    assert run.status == "success" and run.stage_version == "1.0.0"
    assert run.input_hashes == sorted([s6_volume.sha256, s6_meta.sha256])
    assert run.parameters["deface"]["required"] is True
    assert run.parameters["deface"]["reason"] == "body_part=HEAD"
    assert run.parameters["algorithm"]["method"] == "geometric_v1"
    assert run.parameters["series_id"] == str(sid)

    (defaced,) = _artifacts(pipeline_session, sid, "volume_defaced")
    (report,) = _artifacts(pipeline_session, sid, "deface_report")
    assert defaced.produced_by_run == report.produced_by_run == run.id
    assert defaced.object_key != s6_volume.object_key  # silver stays immutable
    assert defaced.zone == "silver" and defaced.contract_version == "1.0"
    assert run.output_hashes == [defaced.sha256, report.sha256]

    stored = get_bytes(silver_minio_client, settings.minio_silver_bucket, defaced.object_key)
    assert _sha(stored) == defaced.sha256
    source = get_bytes(silver_minio_client, settings.minio_silver_bucket, s6_volume.object_key)
    assert _sha(source) == s6_volume.sha256  # the S6 volume is byte-identical to before
    assert nifti_io.header_bytes(stored) == nifti_io.header_bytes(source)
    before, after = nifti_io.load_volume(source).array, nifti_io.load_volume(stored).array
    assert (before != after).sum() > 100_000

    body = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, report.object_key)
    )
    assert body["applied"] is True and body["efficacy"]["passed"] is True

    gated = open_defacing_gate(pipeline_session, silver_minio_client, settings, sid)
    assert gated.source == "defaced"
    assert gated.volume_sha256 == defaced.sha256 != s6_volume.sha256
    assert gated.volume_bytes == stored
    assert gated.deface_block() == {
        "required": True,
        "applied": True,
        "method": "geometric_v1",
        "reason": "body_part=HEAD",
    }


@integration
def test_a_rerun_is_idempotent_and_returns_an_equal_result(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    first = deface_series(pipeline_session, silver_minio_client, settings, sid)
    runs = {r.id for r in _s7_runs(pipeline_session, sid)}
    arts = {
        a.id
        for k in ("volume_defaced", "deface_report")
        for a in _artifacts(pipeline_session, sid, k)
    }

    second = deface_series(pipeline_session, silver_minio_client, settings, sid)

    assert second == first
    assert {r.id for r in _s7_runs(pipeline_session, sid)} == runs
    assert {
        a.id
        for k in ("volume_defaced", "deface_report")
        for a in _artifacts(pipeline_session, sid, k)
    } == arts


@integration
def test_the_gate_refuses_a_head_that_has_no_s7_run(pipeline_session, silver_minio_client) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    assert _gate_code(pipeline_session, silver_minio_client, settings, sid) == "GATE_NO_S7_RUN"


@integration
def test_the_gate_refuses_when_the_defaced_object_was_overwritten(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    (defaced,) = _artifacts(pipeline_session, sid, "volume_defaced")
    original = get_bytes(silver_minio_client, settings.minio_silver_bucket, defaced.object_key)
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        defaced.object_key,
        phantom_nifti(BASE),
        "application/gzip",  # the UN-defaced volume, planted at the key
    )
    assert (
        _gate_code(pipeline_session, silver_minio_client, settings, sid)
        == "GATE_DEFACED_HASH_MISMATCH"
    )
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        defaced.object_key,
        original,
        "application/gzip",
    )
    assert (
        open_defacing_gate(pipeline_session, silver_minio_client, settings, sid).source == "defaced"
    )


@integration
def test_a_newer_s6_volume_makes_the_old_defacing_stale_until_s7_reruns(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    (first_defaced,) = _artifacts(pipeline_session, sid, "volume_defaced")

    ph = phantom(BASE)
    arr = ph.volume.copy()
    arr[0, 0, 0] = -999.0  # a new, different S6 volume for the same series
    new_bytes = nifti_bytes(arr, ph.grid.affine_ras)
    stub = make_s6_stub(new_bytes, ph.volume.shape, ph.grid.affine_ras, series_id=sid)
    _add_s6(
        pipeline_session, silver_minio_client, settings, sid, new_bytes, stub.meta_bytes, later=10
    )

    assert _gate_code(pipeline_session, silver_minio_client, settings, sid) == "GATE_S7_RUN_STALE"

    again = deface_series(pipeline_session, silver_minio_client, settings, sid)
    assert again.applied is True
    assert len(_s7_runs(pipeline_session, sid)) == 2
    gated = open_defacing_gate(pipeline_session, silver_minio_client, settings, sid)
    latest = [
        a for a in _artifacts(pipeline_session, sid, "volume_defaced") if a.id != first_defaced.id
    ]
    assert gated.volume_sha256 == latest[0].sha256 != first_defaced.sha256


@integration
def test_a_stale_not_required_flag_is_overridden_and_the_reason_says_why(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings, body_part="CHEST", flag=True)
    result = deface_series(pipeline_session, silver_minio_client, settings, sid)
    assert result.required is True and result.applied is True
    assert result.reason == "study_deface_required_flag=true (body_part=CHEST)"


@integration
def test_a_series_whose_own_header_says_head_is_defaced_even_if_its_study_says_chest(
    pipeline_session, silver_minio_client
) -> None:
    """A mixed CHEST+HEAD study stores body_part='CHEST' (first writer wins) and a flag that may
    be stale; only the series' own silver headers can neutralise that."""
    settings = get_settings()
    sid = _seed_head(
        pipeline_session,
        silver_minio_client,
        settings,
        body_part="CHEST",
        flag=False,
        header_body_parts=["HEAD", "HEAD"],
    )
    result = deface_series(pipeline_session, silver_minio_client, settings, sid)
    assert result.required is True and result.applied is True
    assert result.reason == "series_header_body_part=HEAD (study=CHEST)"
    (run,) = _s7_runs(pipeline_session, sid)
    assert run.parameters["evidence"]["mismatch"] == ["header_disagrees_with_study"]


@integration
@pytest.mark.parametrize("body_part", ["XYZ", "SINUSES", None])
def test_an_unrecognised_or_absent_body_part_is_treated_as_a_head(
    pipeline_session, silver_minio_client, body_part: str | None
) -> None:
    settings = get_settings()
    sid = _seed_head(
        pipeline_session,
        silver_minio_client,
        settings,
        body_part=body_part,
        flag=True,
        source="unresolved" if body_part is None else "tag",
    )
    result = deface_series(pipeline_session, silver_minio_client, settings, sid)
    assert result.required is True and result.applied is True
    assert result.reason


@integration
def test_a_required_mr_series_is_quarantined_and_the_gate_refuses_it(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    arr = np.zeros((6, 6, 6), dtype=np.float32)
    sid = _seed_series(
        pipeline_session,
        silver_minio_client,
        settings,
        volume_bytes=nifti_bytes(arr, np.eye(4)),
        shape=(6, 6, 6),
        affine=np.eye(4),
        modality="MR",
        body_part="HEAD",
    )
    result = deface_series(pipeline_session, silver_minio_client, settings, sid)

    assert result.quarantined and result.reason_code == "S7_MODALITY_UNSUPPORTED"
    (run,) = _s7_runs(pipeline_session, sid)
    assert run.status == "failed" and run.reason == "S7_MODALITY_UNSUPPORTED"
    q = (
        pipeline_session.execute(select(Quarantine).where(Quarantine.subject_id == sid))
        .scalars()
        .all()
    )
    assert [(x.stage, x.reason_code) for x in q] == [("S7", "S7_MODALITY_UNSUPPORTED")]
    assert _artifacts(pipeline_session, sid, "volume_defaced") == []
    assert (
        _gate_code(pipeline_session, silver_minio_client, settings, sid)
        == "GATE_S7_RUN_NOT_SUCCESS"
    )


@integration
def test_a_required_volume_with_no_head_is_quarantined_not_passed_through(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    v = np.full((160, 200, 320), -1000.0, dtype=np.float32)
    v[5:155, 5:195, 5:315] = 40.0
    v[40:120, 40:160, 60:260] = 1000.0
    sid = _seed_series(
        pipeline_session,
        silver_minio_client,
        settings,
        volume_bytes=nifti_bytes(v, np.eye(4)),
        shape=v.shape,
        affine=np.eye(4),
        body_part="HEAD",
    )
    result = deface_series(pipeline_session, silver_minio_client, settings, sid)
    assert result.quarantined and result.reason_code == "S7_NO_HEAD_FOUND"
    assert (
        _gate_code(pipeline_session, silver_minio_client, settings, sid)
        == "GATE_S7_RUN_NOT_SUCCESS"
    )


@integration
def test_series_that_are_not_ready_are_skipped_with_distinct_records(
    pipeline_session, silver_minio_client
) -> None:
    """Skip keys exclude subject_id, so two such series in one run must carry their identity in
    params -- otherwise the second collides on transform_runs.idempotency_key."""
    settings = get_settings()
    kw = dict(volume_bytes=b"", shape=(1, 1, 1), affine=np.eye(4))
    no_s6 = [
        _seed_series(pipeline_session, silver_minio_client, settings, with_s6=False, **kw)  # type: ignore[arg-type]
        for _ in range(2)
    ]
    failed_gate = _seed_series(
        pipeline_session,
        silver_minio_client,
        settings,
        with_s6=False,
        quality="quarantined",
        **kw,  # type: ignore[arg-type]
    )
    results = [
        deface_series(pipeline_session, silver_minio_client, settings, s)
        for s in [*no_s6, failed_gate]
    ]
    assert [r.skip_reason for r in results] == [
        "series_has_no_s6_volume",
        "series_has_no_s6_volume",
        "series_not_passed_quality_gate",
    ]
    for s in [*no_s6, failed_gate]:
        (run,) = _s7_runs(pipeline_session, s)
        assert run.status == "skipped" and run.reason
    # and a re-run over the same skipped series is a no-op, not a UNIQUE violation
    assert deface_series(pipeline_session, silver_minio_client, settings, no_s6[0]).skipped
    assert len(_s7_runs(pipeline_session, no_s6[0])) == 1


@integration
def test_an_unknown_series_id_is_a_value_error(pipeline_session, silver_minio_client) -> None:
    with pytest.raises(ValueError):
        deface_series(pipeline_session, silver_minio_client, get_settings(), uuid4())


# ---- the real S1 -> S6 path for a chest CT (TESTING.md Proof 2, the not-required half) ----


def _chest_through_s6(session, bronze, silver, settings, tmp_path: Path, name: str) -> UUID:  # type: ignore[no-untyped-def]
    blobs = build_clean_ct_series(count=4, body_part="CHEST")
    archive = build_zip(tmp_path, name, {f"scan{i}.dcm": b for i, b in enumerate(blobs)})
    batch = ingest(session, bronze, settings, "pytest", str(archive))
    parse_and_index_batch(session, bronze, settings, batch.batch_id)
    quality_gate_batch(session, bronze, settings, batch.batch_id)
    deid = deidentify_and_verify_batch(session, bronze, silver, settings, batch.batch_id)
    series_id = deid.series_results[0].series_id
    assert assemble_volume_series(session, silver, settings, series_id).volume_object_key
    return series_id


@integration
def test_a_real_chest_series_is_recorded_as_not_required_with_the_reason_and_served_undefaced(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    sid = _chest_through_s6(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "a.zip"
    )
    (s6_volume,) = _artifacts(pipeline_session, sid, "volume_nifti")

    result = deface_series(pipeline_session, silver_minio_client, settings, sid)

    assert result.required is False and result.applied is False and result.method == "none"
    assert result.reason == "body_part=CHEST"
    (run,) = _s7_runs(pipeline_session, sid)
    assert run.status == "success"
    assert run.parameters["deface"]["required"] is False
    assert run.parameters["deface"]["reason"] == "body_part=CHEST"  # TESTING.md Proof 2
    assert "algorithm" not in run.parameters
    assert _artifacts(pipeline_session, sid, "volume_defaced") == []
    (report,) = _artifacts(pipeline_session, sid, "deface_report")
    assert run.output_hashes == [report.sha256]

    gated = open_defacing_gate(pipeline_session, silver_minio_client, settings, sid)
    assert gated.source == "s6_not_required"
    assert gated.volume_sha256 == s6_volume.sha256
    assert gated.deface_block() == {
        "required": False,
        "applied": False,
        "method": "none",
        "reason": "body_part=CHEST",
    }
    assert deface_series(pipeline_session, silver_minio_client, settings, sid) == result


@integration
def test_a_chest_series_nobody_evaluated_is_refused_by_the_gate_not_assumed_non_head(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    sid = _chest_through_s6(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "b.zip"
    )
    assert _gate_code(pipeline_session, silver_minio_client, settings, sid) == "GATE_NO_S7_RUN"


@integration
def test_deface_batch_covers_every_series_of_a_real_batch(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    blobs = build_clean_ct_series(count=4, body_part="CHEST")
    archive = build_zip(tmp_path, "c.zip", {f"scan{i}.dcm": b for i, b in enumerate(blobs)})
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
    parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    quality_gate_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    deid = deidentify_and_verify_batch(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, batch.batch_id
    )
    sid = deid.series_results[0].series_id
    assemble_volume_series(pipeline_session, silver_minio_client, settings, sid)

    out = deface_batch(pipeline_session, silver_minio_client, settings, batch.batch_id)

    assert [r.series_id for r in out.series_results] == [sid]
    assert out.defaced_count == 0 and out.not_required_count == 1
