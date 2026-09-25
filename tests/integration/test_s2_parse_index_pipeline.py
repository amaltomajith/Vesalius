from __future__ import annotations

from pathlib import Path

from pipeline.catalog.models import Instance, Series, Study
from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_series
from tests.fixtures.shared import integration


@integration
def test_parse_index_populates_study_series_instances(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_series(count=3, body_part="CHEST")
    archive = build_zip(
        tmp_path, "chest.zip", {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))

    result = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    assert result.quarantined_count == 0
    assert len(result.indexed) == 3

    series_id = result.indexed[0].series_id
    study_id = result.indexed[0].study_id
    assert all(r.series_id == series_id for r in result.indexed)
    assert all(r.study_id == study_id for r in result.indexed)

    series = pipeline_session.get(Series, series_id)
    assert series is not None
    assert series.instance_count == 3
    assert series.quality_status == "pending"

    study = pipeline_session.get(Study, study_id)
    assert study is not None
    assert study.body_part == "CHEST"
    assert study.deface_required is False

    for r in result.indexed:
        instance = pipeline_session.get(Instance, r.instance_id)
        assert instance is not None
        assert instance.content_hash == r.content_hash


@integration
def test_head_body_part_requires_defacing(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_series(count=1, body_part="HEAD")
    archive = build_zip(tmp_path, "head.zip", {"scan.dcm": series_bytes[0]})
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))

    result = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    study = pipeline_session.get(Study, result.indexed[0].study_id)
    assert study is not None
    assert study.deface_required is True
    assert study.body_part_source == "tag"


@integration
def test_unresolved_body_part_defaults_deface_required_true(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    series_bytes = build_series(count=1, body_part=None)
    archive = build_zip(tmp_path, "unknown.zip", {"scan.dcm": series_bytes[0]})
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))

    result = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    study = pipeline_session.get(Study, result.indexed[0].study_id)
    assert study is not None
    assert study.deface_required is True  # fail closed
    assert study.body_part_source == "unresolved"
