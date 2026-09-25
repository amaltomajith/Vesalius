"""S9 against live Postgres + MinIO, on top of REAL S7 and S8 runs (the gate's refusal/staleness
behaviour is S7's, already exhaustively tested in test_defacing_gate.py and
test_s7_deface_pipeline.py; S8's segmentation behaviour is its own -- here the point is that S9
calls both correctly, and TESTING.md Proof 2's runtime half: `skip_deface=True` never bypasses
the gate).

Two sources of series, mirroring test_s7/s8_..._pipeline.py: (1) a synthetic head phantom with S6
seeded directly and S7+S8 run for real, and (2) the real S1->S8 path for a chest CT.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from pipeline.catalog.models import Artifact, Series, Study, TransformRun
from pipeline.common.config import get_settings
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s6_volume_assembly.service import assemble_volume_series
from pipeline.stages.s7_deface.service import deface_series
from pipeline.stages.s8_segment.service import segment_series
from pipeline.stages.s9_surface_extraction.mesh_io import load_mesh
from pipeline.stages.s9_surface_extraction.service import (
    extract_surface,
    extract_surface_batch,
)
from pipeline.storage.object_store import get_bytes, put_bytes
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.deface_runs import make_s6_stub, phantom, phantom_nifti
from tests.fixtures.dicom_factory import build_clean_ct_series
from tests.fixtures.shared import integration

BASE = "lps_native_default"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _add_s6(session, silver, settings, series_id: UUID, volume_bytes, meta_bytes):  # type: ignore[no-untyped-def]
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
            started_at=datetime.now(UTC),
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


def _seed_head(session, silver, settings, *, body_part="HEAD", flag=True) -> UUID:  # type: ignore[no-untyped-def]
    study = Study(
        id=uuid4(),
        pseudo_study_uid=f"2.25.{uuid4().int}",
        pseudo_patient_id="P",
        modality="CT",
        body_part=body_part,
        body_part_source="tag",
        deface_required=flag,
    )
    session.add(study)
    session.flush()
    series = Series(
        id=uuid4(),
        study_id=study.id,
        pseudo_series_uid=f"2.25.{uuid4().int}",
        modality="CT",
        instance_count=0,
        quality_status="pass",
    )
    session.add(series)
    session.flush()
    ph = phantom(BASE)
    volume_bytes = phantom_nifti(BASE)
    stub = make_s6_stub(volume_bytes, ph.volume.shape, ph.grid.affine_ras, series_id=series.id)
    _add_s6(session, silver, settings, series.id, volume_bytes, stub.meta_bytes)
    return series.id


def _artifacts(session, series_id: UUID, kind: str) -> list[Artifact]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(Artifact).where(Artifact.series_id == series_id, Artifact.kind == kind)
        ).scalars()
    )


def _s9_runs(session, series_id: UUID) -> list[TransformRun]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S9", TransformRun.subject_id == series_id
            )
        ).scalars()
    )


def _head_through_s8(session, silver, settings, preset="bone"):  # type: ignore[no-untyped-def]
    sid = _seed_head(session, silver, settings)
    deface_series(session, silver, settings, sid)
    segment_series(session, silver, settings, sid, preset)
    return sid


@integration
def test_a_head_mesh_is_extracted_after_real_s7_and_s8_runs(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s8(pipeline_session, silver_minio_client, settings)

    result = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert not result.skipped and not result.quarantined
    assert result.mesh_object_key is not None
    assert result.vertex_count and result.vertex_count > 0
    assert result.face_count and result.face_count > 0

    (run,) = _s9_runs(pipeline_session, sid)
    assert run.status == "success"
    assert run.parameters["deface"]["required"] is True
    assert run.parameters["algorithm"]["preset"] == "bone"
    assert run.parameters["series_id"] == str(sid)
    assert run.parameters["skip_deface"] is False

    (mask_art,) = _artifacts(pipeline_session, sid, "mask")
    (defaced_art,) = _artifacts(pipeline_session, sid, "volume_defaced")
    (s6_meta_art,) = _artifacts(pipeline_session, sid, "volume_meta")
    assert run.input_hashes == sorted([defaced_art.sha256, s6_meta_art.sha256, mask_art.sha256])

    (mesh_art,) = _artifacts(pipeline_session, sid, "surface_mesh_raw")
    (report_art,) = _artifacts(pipeline_session, sid, "surface_extraction_report")
    assert mesh_art.produced_by_run == report_art.produced_by_run == run.id
    assert mesh_art.zone == "silver" and mesh_art.format == "ply"
    assert run.output_hashes == [mesh_art.sha256, report_art.sha256]

    stored = get_bytes(silver_minio_client, settings.minio_silver_bucket, mesh_art.object_key)
    assert _sha(stored) == mesh_art.sha256
    verts, faces = load_mesh(stored)
    assert len(verts) == result.vertex_count
    assert len(faces) == result.face_count

    body = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, report_art.object_key)
    )
    assert body["deface"]["reason"] == "body_part=HEAD"
    assert body["stats"]["vertex_count"] == result.vertex_count


@integration
def test_a_rerun_of_the_same_preset_is_idempotent_and_returns_an_equal_result(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s8(pipeline_session, silver_minio_client, settings)

    first = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")
    runs = {r.id for r in _s9_runs(pipeline_session, sid)}
    arts = {a.id for a in _artifacts(pipeline_session, sid, "surface_mesh_raw")}

    second = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert second == first
    assert {r.id for r in _s9_runs(pipeline_session, sid)} == runs
    assert {a.id for a in _artifacts(pipeline_session, sid, "surface_mesh_raw")} == arts


@integration
def test_a_rerun_over_a_succeeded_run_with_a_missing_mesh_artifact_crashes_loudly(
    pipeline_session, silver_minio_client
) -> None:
    """Guards the idempotency invariant itself (S8's identical test): a successful transform_run
    must always have its artifacts. A rerun over the SAME inputs takes the "already succeeded,
    skip the work" path -- if the catalog is ever corrupted so the artifact is missing, that
    path must fail loudly, never silently return a result with no mesh."""
    settings = get_settings()
    sid = _head_through_s8(pipeline_session, silver_minio_client, settings)
    extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    (mesh_art,) = _artifacts(pipeline_session, sid, "surface_mesh_raw")
    pipeline_session.delete(mesh_art)
    pipeline_session.flush()

    with pytest.raises(AssertionError, match="idempotency invariant violated"):
        extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")


@integration
def test_a_head_with_no_s8_mask_for_the_preset_is_skipped_not_quarantined(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    # no S8 run at all yet

    result = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.skipped and not result.quarantined
    assert result.skip_reason == "series_has_no_s8_mask"
    (run,) = _s9_runs(pipeline_session, sid)
    assert run.status == "skipped"
    assert run.parameters["series_id"] == str(sid)
    assert _artifacts(pipeline_session, sid, "surface_mesh_raw") == []


@integration
def test_a_head_with_a_mask_for_a_different_preset_is_still_skipped(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    segment_series(pipeline_session, silver_minio_client, settings, sid, "skin_surface")

    result = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.skipped
    assert result.skip_reason == "series_has_no_s8_mask"


@integration
def test_a_head_with_no_s7_run_is_skipped_by_the_gate_refusal(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    # no S7 run at all

    result = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.skipped
    assert result.gate_reason_code == "GATE_NO_S7_RUN"
    assert result.skip_reason == "defacing_gate_refused:GATE_NO_S7_RUN"
    (run,) = _s9_runs(pipeline_session, sid)
    assert run.status == "skipped"


@integration
def test_proof_2_skip_deface_true_still_cannot_produce_a_mesh_for_an_undefaced_head(
    pipeline_session, silver_minio_client
) -> None:
    """TESTING.md Proof 2's literal shape is `pytest.raises(DefacingGateError)`; this codebase's
    established pattern (S7/S8) is that EVERY gate refusal becomes a `skipped` transform_run
    result at the orchestration level, never an exception the caller must catch -- `extract_surface`
    follows the same pattern, consistently, rather than being a special case. The formal
    `tests/proofs/test_defacing_gate.py` (deferred, like S7's plan explicitly deferred it) picks
    the final literal assertion shape; what's proven here and now is the actual claim: passing
    `skip_deface=True` to an undefaced head still produces NO mesh and NO mesh artifact -- it
    changes nothing about the gate's verdict. The static half (skip_deface never referenced
    inside the gate-call branch at all) lives in tests/unit/test_s9_service_stubs.py.
    """
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    # deliberately no S7 run -- an "undefaced head" in the sense the gate cares about

    with_flag = extract_surface(
        pipeline_session, silver_minio_client, settings, sid, "bone", skip_deface=True
    )
    without_flag = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    for result in (with_flag, without_flag):
        assert result.skipped
        assert result.gate_reason_code == "GATE_NO_S7_RUN"
        assert result.mesh_object_key is None
    assert _artifacts(pipeline_session, sid, "surface_mesh_raw") == []


@integration
def test_a_tampered_mask_object_is_refused(pipeline_session, silver_minio_client) -> None:
    settings = get_settings()
    sid = _head_through_s8(pipeline_session, silver_minio_client, settings)
    (mask_art,) = _artifacts(pipeline_session, sid, "mask")
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        mask_art.object_key,
        b"corrupted",
        "application/gzip",
    )

    result = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.quarantined
    assert result.reason_code == "S9_INPUT_TAMPERED"


@integration
def test_two_series_lacking_an_s8_mask_in_one_run_get_distinct_skip_rows(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sids = []
    for _ in range(2):
        sid = _seed_head(pipeline_session, silver_minio_client, settings)
        deface_series(pipeline_session, silver_minio_client, settings, sid)
        sids.append(sid)

    results = [
        extract_surface(pipeline_session, silver_minio_client, settings, s, "bone") for s in sids
    ]
    assert all(r.skipped for r in results)
    for s in sids:
        assert len(_s9_runs(pipeline_session, s)) == 1
    again = extract_surface(pipeline_session, silver_minio_client, settings, sids[0], "bone")
    assert again.skipped
    assert len(_s9_runs(pipeline_session, sids[0])) == 1


@integration
def test_an_unknown_series_id_is_a_value_error(pipeline_session, silver_minio_client) -> None:
    with pytest.raises(ValueError):
        extract_surface(pipeline_session, silver_minio_client, get_settings(), uuid4(), "bone")


# ---- the real S1 -> S8 path for a chest CT -----------------------------------------------------


def _chest_through_s8(session, bronze, silver, settings, tmp_path: Path, name: str):  # type: ignore[no-untyped-def]
    blobs = build_clean_ct_series(
        count=10,
        body_part="CHEST",
        uniform_raw=1024,
        rows=20,
        columns=20,
        pixel_spacing=(1.0, 1.0),
    )
    archive = build_zip(tmp_path, name, {f"scan{i}.dcm": b for i, b in enumerate(blobs)})
    batch = ingest(session, bronze, settings, "pytest", str(archive))
    parse_and_index_batch(session, bronze, settings, batch.batch_id)
    quality_gate_batch(session, bronze, settings, batch.batch_id)
    deid = deidentify_and_verify_batch(session, bronze, silver, settings, batch.batch_id)
    series_id = deid.series_results[0].series_id
    assemble_volume_series(session, silver, settings, series_id)
    deface_series(session, silver, settings, series_id)
    segment_series(session, silver, settings, series_id, "soft_tissue")
    return series_id, batch.batch_id


@integration
def test_a_real_chest_series_soft_tissue_mesh_extracts_successfully(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    sid, batch_id = _chest_through_s8(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "c.zip"
    )

    result = extract_surface(pipeline_session, silver_minio_client, settings, sid, "soft_tissue")

    assert not result.skipped and not result.quarantined
    assert result.mesh_object_key is not None
    assert result.vertex_count and result.vertex_count > 0

    out = extract_surface_batch(
        pipeline_session, silver_minio_client, settings, batch_id, presets=("soft_tissue",)
    )
    assert [r.series_id for r in out.series_results] == [sid]
    assert out.extracted_count == 1
