"""Real, end-to-end OCR tests -- actual tesseract, not mocked. Confidence-threshold *logic*
is covered deterministically in tests/unit/test_pixel_redaction_logic.py; this file only
proves the real pipeline actually detects and redacts genuine burned-in text.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s4_deidentify.service import deidentify_instance
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_clean_ct_series
from tests.fixtures.shared import integration, ocr


def _ingest_clean_passed_series(session, bronze_minio_client, tmp_path: Path, **kwargs):
    settings = get_settings()
    series_bytes = build_clean_ct_series(count=1, body_part="CHEST", **kwargs)
    archive = build_zip(tmp_path, "clean.zip", {"scan0.dcm": series_bytes[0]})
    batch = ingest(session, bronze_minio_client, settings, "pytest", str(archive))
    parse_result = parse_and_index_batch(session, bronze_minio_client, settings, batch.batch_id)
    quality_gate_batch(session, bronze_minio_client, settings, batch.batch_id)
    return parse_result


@integration
@ocr
def test_burned_in_text_is_detected_and_redacted(
    pipeline_session, bronze_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    parse_result = _ingest_clean_passed_series(
        pipeline_session,
        bronze_minio_client,
        tmp_path,
        rows=200,
        columns=200,
        burned_text="TESTPHI PATIENT",
    )
    instance_id = parse_result.indexed[0].instance_id

    result = deidentify_instance(pipeline_session, bronze_minio_client, settings, instance_id)

    pixel_records = [r for r in result.audit_records if r.channel == "pixel"]
    assert any(r.pixel_region is not None for r in pixel_records), (
        "expected at least one redacted pixel region for clearly burned-in text -- if this "
        "fails, the OCR confidence thresholds in pixel_redaction.py likely need retuning "
        "(they are explicitly documented as placeholders pending real tuning)"
    )
    assert result.dataset.BurnedInAnnotation == "NO"


@integration
@ocr
def test_clean_pixel_data_sets_burned_in_annotation_no_with_audit_row(
    pipeline_session, bronze_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    parse_result = _ingest_clean_passed_series(
        pipeline_session, bronze_minio_client, tmp_path, rows=64, columns=64
    )
    instance_id = parse_result.indexed[0].instance_id

    result = deidentify_instance(pipeline_session, bronze_minio_client, settings, instance_id)

    pixel_records = [r for r in result.audit_records if r.channel == "pixel"]
    assert len(pixel_records) == 1
    assert pixel_records[0].pixel_region is None  # nothing redacted
    assert result.dataset.BurnedInAnnotation == "NO"
