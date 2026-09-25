"""S10 against live Postgres + MinIO, on top of REAL S7, S8 and S9 runs (no defacing gate call
of its own -- the mesh is already gate-derived provenance; here the point is that S10 finds S9's
mesh correctly and carries the `deface` block forward).

Two sources of series, mirroring test_s7/s8/s9_..._pipeline.py: (1) a synthetic head phantom with
S6 seeded directly and S7+S8+S9 run for real, and (2) the real S1->S9 path for a chest CT.
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
from pipeline.stages.s9_surface_extraction.service import extract_surface
from pipeline.stages.s10_mesh_postprocess.mesh_io import load_mesh
from pipeline.stages.s10_mesh_postprocess.service import (
    postprocess_mesh_batch,
    postprocess_mesh_series,
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


def _s10_runs(session, series_id: UUID) -> list[TransformRun]:  # type: ignore[no-untyped-def]
    return list(
        session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S10", TransformRun.subject_id == series_id
            )
        ).scalars()
    )


def _head_through_s9(session, silver, settings, preset="bone"):  # type: ignore[no-untyped-def]
    sid = _seed_head(session, silver, settings)
    deface_series(session, silver, settings, sid)
    segment_series(session, silver, settings, sid, preset)
    extract_surface(session, silver, settings, sid, preset)
    return sid


@integration
def test_a_head_mesh_is_postprocessed_after_real_s7_s8_s9_runs(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s9(pipeline_session, silver_minio_client, settings)
    (raw_art,) = _artifacts(pipeline_session, sid, "surface_mesh_raw")

    result = postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sid, "bone", target_faces=50_000
    )

    assert not result.skipped and not result.quarantined
    assert result.mesh_object_key is not None
    assert result.input_faces and result.final_face_count
    assert result.final_face_count <= 50_000
    assert result.final_face_count < result.input_faces

    (run,) = _s10_runs(pipeline_session, sid)
    assert run.status == "success"
    assert run.parameters["deface"]["required"] is True
    assert run.parameters["deface"]["reason"] == "body_part=HEAD"
    assert run.parameters["algorithm"]["target_faces"] == 50_000
    assert run.parameters["algorithm"]["taubin_nu"] == 0.53
    assert run.parameters["series_id"] == str(sid)
    assert run.input_hashes == [raw_art.sha256]

    (mesh_art,) = _artifacts(pipeline_session, sid, "surface_mesh_optimized")
    (report_art,) = _artifacts(pipeline_session, sid, "mesh_postprocess_report")
    assert mesh_art.produced_by_run == report_art.produced_by_run == run.id
    assert mesh_art.zone == "silver" and mesh_art.format == "ply"
    assert run.output_hashes == [mesh_art.sha256, report_art.sha256]

    stored = get_bytes(silver_minio_client, settings.minio_silver_bucket, mesh_art.object_key)
    assert _sha(stored) == mesh_art.sha256
    verts, faces, normals = load_mesh(stored)
    assert len(verts) == normals.shape[0]
    assert len(faces) == result.final_face_count

    body = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, report_art.object_key)
    )
    assert body["deface"]["reason"] == "body_part=HEAD"
    assert body["stats"]["final_face_count"] == result.final_face_count


@integration
def test_a_rerun_of_the_same_preset_is_idempotent_and_returns_an_equal_result(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s9(pipeline_session, silver_minio_client, settings)

    first = postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sid, "bone", target_faces=50_000
    )
    runs = {r.id for r in _s10_runs(pipeline_session, sid)}
    arts = {a.id for a in _artifacts(pipeline_session, sid, "surface_mesh_optimized")}

    second = postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sid, "bone", target_faces=50_000
    )

    assert second == first
    assert {r.id for r in _s10_runs(pipeline_session, sid)} == runs
    assert {a.id for a in _artifacts(pipeline_session, sid, "surface_mesh_optimized")} == arts


@integration
def test_a_rerun_over_a_succeeded_run_with_a_missing_mesh_artifact_crashes_loudly(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s9(pipeline_session, silver_minio_client, settings)
    postprocess_mesh_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    (mesh_art,) = _artifacts(pipeline_session, sid, "surface_mesh_optimized")
    pipeline_session.delete(mesh_art)
    pipeline_session.flush()

    with pytest.raises(AssertionError, match="idempotency invariant violated"):
        postprocess_mesh_series(pipeline_session, silver_minio_client, settings, sid, "bone")


@integration
def test_a_head_with_no_s9_mesh_for_the_preset_is_skipped_not_quarantined(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")
    # no S9 run at all

    result = postprocess_mesh_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.skipped and not result.quarantined
    assert result.skip_reason == "series_has_no_s9_mesh"
    (run,) = _s10_runs(pipeline_session, sid)
    assert run.status == "skipped"
    assert run.parameters["series_id"] == str(sid)
    assert _artifacts(pipeline_session, sid, "surface_mesh_optimized") == []


@integration
def test_a_head_with_a_mesh_for_a_different_preset_is_still_skipped(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    segment_series(pipeline_session, silver_minio_client, settings, sid, "skin_surface")
    extract_surface(pipeline_session, silver_minio_client, settings, sid, "skin_surface")

    result = postprocess_mesh_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.skipped
    assert result.skip_reason == "series_has_no_s9_mesh"


@integration
def test_a_tampered_raw_mesh_object_is_refused(pipeline_session, silver_minio_client) -> None:
    settings = get_settings()
    sid = _head_through_s9(pipeline_session, silver_minio_client, settings)
    (raw_art,) = _artifacts(pipeline_session, sid, "surface_mesh_raw")
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        raw_art.object_key,
        b"corrupted",
        "application/octet-stream",
    )

    result = postprocess_mesh_series(pipeline_session, silver_minio_client, settings, sid, "bone")

    assert result.quarantined
    assert result.reason_code == "S10_INPUT_TAMPERED"


@integration
def test_two_series_lacking_an_s9_mesh_in_one_run_get_distinct_skip_rows(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    sids = []
    for _ in range(2):
        sid = _seed_head(pipeline_session, silver_minio_client, settings)
        deface_series(pipeline_session, silver_minio_client, settings, sid)
        segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")
        sids.append(sid)

    results = [
        postprocess_mesh_series(pipeline_session, silver_minio_client, settings, s, "bone")
        for s in sids
    ]
    assert all(r.skipped for r in results)
    for s in sids:
        assert len(_s10_runs(pipeline_session, s)) == 1
    again = postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sids[0], "bone"
    )
    assert again.skipped
    assert len(_s10_runs(pipeline_session, sids[0])) == 1


@integration
def test_an_unknown_series_id_is_a_value_error(pipeline_session, silver_minio_client) -> None:
    with pytest.raises(ValueError):
        postprocess_mesh_series(
            pipeline_session, silver_minio_client, get_settings(), uuid4(), "bone"
        )


# ---- the real S1 -> S9 path for a chest CT -----------------------------------------------------


def _chest_through_s9(session, bronze, silver, settings, tmp_path: Path, name: str):  # type: ignore[no-untyped-def]
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
    extract_surface(session, silver, settings, series_id, "soft_tissue")
    return series_id, batch.batch_id


@integration
def test_a_real_chest_series_soft_tissue_mesh_postprocesses_successfully(
    pipeline_session, bronze_minio_client, silver_minio_client, no_ocr_engine, tmp_path: Path
) -> None:
    settings = get_settings()
    sid, batch_id = _chest_through_s9(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "c.zip"
    )

    result = postprocess_mesh_series(
        pipeline_session, silver_minio_client, settings, sid, "soft_tissue"
    )

    assert not result.skipped and not result.quarantined
    assert result.mesh_object_key is not None
    assert result.final_face_count and result.final_face_count > 0

    out = postprocess_mesh_batch(
        pipeline_session, silver_minio_client, settings, batch_id, presets=("soft_tissue",)
    )
    assert [r.series_id for r in out.series_results] == [sid]
    assert out.processed_count == 1
