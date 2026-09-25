"""Duplicate content detection spans batches: `_prior_success_exists` queries by
subject_id+stage across every S1 transform_run, not just the current batch's. This
requires a real Postgres (JSONB/ARRAY/UUID columns don't run on SQLite), so it is gated
behind PIPELINE_INTEGRATION_TESTS even though it lives under tests/unit/ per the plan's
file layout -- see tests/fixtures/shared.py.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from tests.fixtures.dicom_factory import build_dicom_bytes
from tests.fixtures.shared import integration


@integration
def test_same_content_ingested_twice_is_marked_duplicate(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    data = build_dicom_bytes()

    dir_a = tmp_path / "batch_a"
    dir_a.mkdir()
    (dir_a / "scan.dcm").write_bytes(data)

    dir_b = tmp_path / "batch_b"
    dir_b.mkdir()
    (dir_b / "scan.dcm").write_bytes(data)

    result_a = ingest(pipeline_session, bronze_minio_client, settings, "batch-a", str(dir_a))
    result_b = ingest(pipeline_session, bronze_minio_client, settings, "batch-b", str(dir_b))

    assert result_a.files[0].duplicate is False
    assert result_b.files[0].duplicate is True
    assert result_a.files[0].content_hash == result_b.files[0].content_hash
    assert result_a.files[0].instance_subject_id == result_b.files[0].instance_subject_id


@integration
def test_duplicate_within_same_batch_only_writes_bronze_once(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    data = build_dicom_bytes()

    source_dir = tmp_path / "batch_with_dupe"
    source_dir.mkdir()
    (source_dir / "scan1.dcm").write_bytes(data)
    (source_dir / "scan2.dcm").write_bytes(data)  # identical bytes, different filename

    result = ingest(pipeline_session, bronze_minio_client, settings, "dupe-batch", str(source_dir))

    duplicates = [f for f in result.files if f.duplicate]
    assert len(duplicates) == 1
