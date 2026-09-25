from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from pipeline.catalog.models import IngestBatch, Instance, TransformRun
from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from pipeline.storage.bronze import bronze_exists
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_series
from tests.fixtures.shared import integration


@integration
def test_ingest_archive_lands_bronze_objects_and_records_batch(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_series(count=3)
    archive = build_zip(
        tmp_path,
        "study.zip",
        {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)},
    )

    result = ingest(pipeline_session, bronze_minio_client, settings, "pytest-source", str(archive))

    assert result.file_count == 3
    assert result.non_dicom_skipped == 0
    for file_result in result.files:
        assert bronze_exists(bronze_minio_client, settings, file_result.content_hash)
        assert file_result.duplicate is False

    batch = pipeline_session.get(IngestBatch, result.batch_id)
    assert batch is not None
    assert batch.file_count == 3
    assert batch.completed_at is not None


@integration
def test_ingest_does_not_write_instances_rows(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    """S1 does not own `instances` -- series_id is NOT NULL and only S2 can resolve it."""
    settings = get_settings()
    archive = build_zip(tmp_path, "single.zip", {"scan.dcm": build_series(count=1)[0]})

    result = ingest(pipeline_session, bronze_minio_client, settings, "pytest-source", str(archive))

    subject_id = result.files[0].instance_subject_id
    instance = pipeline_session.get(Instance, subject_id)
    assert instance is None


@integration
def test_ingest_records_transform_run_per_instance_and_batch(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    archive = build_zip(tmp_path, "single.zip", {"scan.dcm": build_series(count=1)[0]})

    result = ingest(pipeline_session, bronze_minio_client, settings, "pytest-source", str(archive))

    instance_runs = (
        pipeline_session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S1",
                TransformRun.subject_type == "instance",
                TransformRun.subject_id == result.files[0].instance_subject_id,
            )
        )
        .scalars()
        .all()
    )
    assert any(r.status == "success" for r in instance_runs)

    batch_run = pipeline_session.execute(
        select(TransformRun).where(
            TransformRun.stage == "S1",
            TransformRun.subject_type == "batch",
            TransformRun.subject_id == result.batch_id,
        )
    ).scalar_one_or_none()
    assert batch_run is not None
    assert batch_run.status == "success"
