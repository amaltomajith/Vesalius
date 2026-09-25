from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from pipeline.catalog.models import Quarantine, TransformRun
from pipeline.common.config import get_settings
from pipeline.common.errors import UnreadableArchiveError, ZeroDicomFoundError
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from tests.fixtures.archive_builder import build_corrupt_zip, build_zip
from tests.fixtures.dicom_factory import build_clean_ct_series
from tests.fixtures.shared import integration


@integration
def test_unreadable_archive_quarantines_at_batch_level(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    archive = build_corrupt_zip(tmp_path, "bad.zip")

    with pytest.raises(UnreadableArchiveError):
        ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))

    # Scoped to this test's own archive path (present in `detail`) rather than a bare
    # global count for the reason code, which would spuriously fail against a database
    # holding any other S1_UNREADABLE_ARCHIVE row (e.g. from a prior manual/CLI run).
    rows = (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.stage == "S1", Quarantine.reason_code == "S1_UNREADABLE_ARCHIVE"
            )
        )
        .scalars()
        .all()
    )
    matching = [r for r in rows if r.detail.get("path") == str(archive)]
    assert len(matching) == 1
    assert matching[0].subject_type == "batch"


@integration
def test_zero_dicom_found_quarantines_at_batch_level(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    archive = build_zip(tmp_path, "junk.zip", {"README.txt": b"not dicom at all"})

    with pytest.raises(ZeroDicomFoundError):
        ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))

    rows = (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.stage == "S1", Quarantine.reason_code == "S1_ZERO_DICOM_FOUND"
            )
        )
        .scalars()
        .all()
    )
    matching = [r for r in rows if r.detail.get("path") == str(archive)]
    assert len(matching) == 1


@integration
def test_s2_corrupt_dataset_quarantines_at_instance_level(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    corrupt = b"\x00" * 128 + b"DICM" + b"\xff\xff\xff\xff" + b"garbage"
    archive = build_zip(tmp_path, "corrupt.zip", {"bad.dcm": corrupt})
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))

    result = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    assert result.quarantined_count == 1

    expected_instance_id = instance_id_for_content_hash(sha256_of_bytes(corrupt))
    rows = (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.stage == "S2",
                Quarantine.subject_type == "instance",
                Quarantine.subject_id == expected_instance_id,
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].detail  # failing detail present, not just a bare code


@integration
def test_s3_quality_gate_passes_clean_series_through_full_pipeline(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=3, body_part="CHEST")
    archive = build_zip(
        tmp_path, "clean.zip", {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
    parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    result = quality_gate_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    assert result.quarantined_count == 0
    assert all(r.quality_status == "pass" for r in result.evaluated)

    series_ids = [r.series_id for r in result.evaluated]
    rows = (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.stage == "S3", Quarantine.subject_id.in_(series_ids)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 0


@integration
def test_s3_quarantines_series_at_series_level(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=2)  # QG_TOO_FEW_SLICES
    archive = build_zip(
        tmp_path, "defective.zip", {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
    parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    result = quality_gate_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    assert result.quarantined_count == 1

    series_ids = [r.series_id for r in result.evaluated]
    rows = (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.stage == "S3",
                Quarantine.subject_type == "series",
                Quarantine.subject_id.in_(series_ids),
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].reason_code == "QG_TOO_FEW_SLICES"
    assert rows[0].detail  # failing detail present, not just a bare code


@integration
def test_s4_skips_series_that_did_not_pass_quality_gate(
    pipeline_session, bronze_minio_client, silver_minio_client, tmp_path: Path
) -> None:
    """No @ocr needed -- the series-not-pass guard fires before S4 ever reads bronze or
    touches OCR at all (STAGES.md: "everything downstream of a quarantine stops").
    """
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=2)  # QG_TOO_FEW_SLICES -> quarantined at S3
    archive = build_zip(
        tmp_path, "defective.zip", {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
    parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    quality_gate_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    result = deidentify_and_verify_batch(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, batch.batch_id
    )

    assert len(result.series_results) == 1
    assert result.series_results[0].skipped is True
    assert result.series_results[0].results == []

    series_id = result.series_results[0].series_id
    skip_runs = (
        pipeline_session.execute(
            select(TransformRun).where(
                TransformRun.stage == "S4",
                TransformRun.subject_type == "series",
                TransformRun.subject_id == series_id,
                TransformRun.status == "skipped",
            )
        )
        .scalars()
        .all()
    )
    assert len(skip_runs) == 1
    assert skip_runs[0].reason == "series_not_passed_quality_gate"


@integration
def test_s4_skips_two_different_quarantined_series_and_tolerates_a_rerun(
    pipeline_session, bronze_minio_client, silver_minio_client, tmp_path: Path
) -> None:
    """Every skipped series needs its OWN transform_run: the idempotency key deliberately
    excludes subject_id, so a skip context without the series' identity collides across
    series (and on a re-run of the same one) on transform_runs.idempotency_key's UNIQUE.
    """
    settings = get_settings()
    series_ids = []
    batches = []
    for n in range(2):  # two distinct series (fresh UIDs each build), both fail S3
        archive = build_zip(
            tmp_path,
            f"defective{n}.zip",
            {f"scan{i}.dcm": d for i, d in enumerate(build_clean_ct_series(count=2))},
        )
        batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
        parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
        quality_gate_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
        batches.append(batch)

    for batch in batches:
        result = deidentify_and_verify_batch(
            pipeline_session, bronze_minio_client, silver_minio_client, settings, batch.batch_id
        )
        series_ids.append(result.series_results[0].series_id)
    # re-running a batch whose series is already recorded as skipped must not crash either
    deidentify_and_verify_batch(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, batches[0].batch_id
    )

    assert series_ids[0] != series_ids[1]
    for sid in series_ids:
        skips = (
            pipeline_session.execute(
                select(TransformRun).where(
                    TransformRun.stage == "S4",
                    TransformRun.subject_type == "series",
                    TransformRun.subject_id == sid,
                    TransformRun.status == "skipped",
                )
            )
            .scalars()
            .all()
        )
        assert len(skips) == 1
