from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from pipeline.catalog.models import Artifact, DeidAudit, Instance, Series, TransformRun
from pipeline.common.config import get_settings
from pipeline.common.hashing import sha256_of_bytes
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_instance
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_clean_ct_series, build_series
from tests.fixtures.shared import integration, ocr


@integration
def test_reingesting_same_directory_produces_no_new_bronze_or_instances(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_series(count=2)
    source_dir = tmp_path / "incoming"
    source_dir.mkdir()
    for i, data in enumerate(series_bytes):
        (source_dir / f"scan{i}.dcm").write_bytes(data)

    first = ingest(pipeline_session, bronze_minio_client, settings, "run-1", str(source_dir))
    second = ingest(pipeline_session, bronze_minio_client, settings, "run-2", str(source_dir))

    assert all(not f.duplicate for f in first.files)
    assert all(f.duplicate for f in second.files)

    content_hashes = [sha256_of_bytes(data) for data in series_bytes]
    instance_count = pipeline_session.execute(
        select(func.count()).select_from(Instance).where(Instance.content_hash.in_(content_hashes))
    ).scalar_one()
    # Nothing has run S2 yet in this test -- instances for this content should still be
    # zero either way, but the assertion that matters here is bronze/transform_run
    # behavior above. Scoped to this test's own content hashes rather than a bare global
    # count, which would spuriously fail against a database holding any other data (e.g.
    # from a prior manual/CLI run against the same dev database).
    assert instance_count == 0


@integration
def test_reparsing_same_batch_is_idempotent_no_duplicate_catalog_rows(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_series(count=2)
    archive = build_zip(
        tmp_path, "study.zip", {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(pipeline_session, bronze_minio_client, settings, "run-1", str(archive))

    first = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    second = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    assert first.indexed[0].instance_id == second.indexed[0].instance_id

    study_id = first.indexed[0].study_id
    series_id = first.indexed[0].series_id
    instance_ids = [r.instance_id for r in first.indexed]

    # Scoped to this test's own study/series/instance ids rather than bare global counts,
    # which would spuriously fail against a database holding any other data.
    series_count = pipeline_session.execute(
        select(func.count()).select_from(Series).where(Series.study_id == study_id)
    ).scalar_one()
    instance_count = pipeline_session.execute(
        select(func.count()).select_from(Instance).where(Instance.series_id == series_id)
    ).scalar_one()
    assert series_count == 1
    assert instance_count == 2

    s2_success_runs = pipeline_session.execute(
        select(func.count())
        .select_from(TransformRun)
        .where(
            TransformRun.stage == "S2",
            TransformRun.status == "success",
            TransformRun.subject_id.in_(instance_ids),
        )
    ).scalar_one()
    assert s2_success_runs == 2  # one per instance, not doubled by the re-run


@integration
@ocr
def test_deidentify_and_verify_is_idempotent_no_duplicate_audit_or_artifact_rows(
    pipeline_session, bronze_minio_client, silver_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=1, body_part="CHEST")
    archive = build_zip(tmp_path, "clean.zip", {"scan0.dcm": series_bytes[0]})
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
    parse_result = parse_and_index_batch(
        pipeline_session, bronze_minio_client, settings, batch.batch_id
    )
    quality_gate_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    instance_id = parse_result.indexed[0].instance_id

    first = deidentify_and_verify_instance(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, instance_id
    )
    audit_count_after_first = pipeline_session.execute(
        select(func.count()).select_from(DeidAudit).where(DeidAudit.instance_id == instance_id)
    ).scalar_one()
    assert audit_count_after_first > 0  # sanity: something was actually recorded

    second = deidentify_and_verify_instance(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, instance_id
    )

    assert first.stage_failed is None
    assert second.stage_failed is None
    assert first.silver_object_key == second.silver_object_key

    audit_count = pipeline_session.execute(
        select(func.count()).select_from(DeidAudit).where(DeidAudit.instance_id == instance_id)
    ).scalar_one()
    assert audit_count == audit_count_after_first  # not duplicated by the second call
    artifact_count = pipeline_session.execute(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.object_key == first.silver_object_key)
    ).scalar_one()
    s5_success_runs = pipeline_session.execute(
        select(func.count())
        .select_from(TransformRun)
        .where(
            TransformRun.stage == "S5",
            TransformRun.status == "success",
            TransformRun.subject_id == instance_id,
        )
    ).scalar_one()

    assert artifact_count == 1  # not duplicated by the second call
    assert s5_success_runs == 1  # not doubled by the re-run
