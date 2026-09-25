"""S4 always runs OCR (channel 3), so every test here needs @ocr + the `ocr_capability`
fixture in addition to @integration -- not just Postgres/MinIO.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from pipeline.catalog.models import DeidAudit, PatientMap, Series, Study
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
    # A fresh PatientID per call: the patient map is global and the dev database keeps committed
    # rows from CLI runs, so a constant ID would already have a finalised (non-provisional)
    # pseudonym and the "provisional -> real" assertions could never hold.
    kwargs.setdefault("patient_id", f"PAT-{uuid4().hex}")
    series_bytes = build_clean_ct_series(count=3, body_part="CHEST", **kwargs)
    archive = build_zip(
        tmp_path, "clean.zip", {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(session, bronze_minio_client, settings, "pytest", str(archive))
    parse_result = parse_and_index_batch(session, bronze_minio_client, settings, batch.batch_id)
    quality_gate_batch(session, bronze_minio_client, settings, batch.batch_id)
    series_id = parse_result.indexed[0].series_id
    series = session.get(Series, series_id)
    assert series.quality_status == "pass"
    return series, parse_result


@integration
@ocr
def test_s4_finalizes_uid_map_from_provisional_to_real(
    pipeline_session, bronze_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session, bronze_minio_client, tmp_path
    )
    study = pipeline_session.get(Study, series.study_id)

    provisional_study_uid = study.pseudo_study_uid
    assert provisional_study_uid.startswith("provisional.")

    instance_id = parse_result.indexed[0].instance_id
    deidentify_instance(pipeline_session, bronze_minio_client, settings, instance_id)

    pipeline_session.refresh(study)
    assert study.pseudo_study_uid.startswith("2.25.")
    assert study.pseudo_study_uid != provisional_study_uid


@integration
@ocr
def test_s4_finalizes_patient_map_to_real_pseudonym(
    pipeline_session, bronze_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session, bronze_minio_client, tmp_path
    )
    study = pipeline_session.get(Study, series.study_id)
    provisional_patient_id = study.pseudo_patient_id
    assert provisional_patient_id.startswith("provisional.")

    instance_id = parse_result.indexed[0].instance_id
    deidentify_instance(pipeline_session, bronze_minio_client, settings, instance_id)

    pipeline_session.refresh(study)
    assert study.pseudo_patient_id.startswith("PP-")
    assert study.pseudo_patient_id != provisional_patient_id

    patient_map_rows = (
        pipeline_session.execute(
            select(PatientMap).where(PatientMap.pseudo_patient_id == study.pseudo_patient_id)
        )
        .scalars()
        .all()
    )
    assert len(patient_map_rows) == 1
    assert patient_map_rows[0].date_offset_days != 0


@integration
@ocr
def test_s4_writes_deid_audit_row_per_must_remove_tag(
    pipeline_session, bronze_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session,
        bronze_minio_client,
        tmp_path,
        extra_tags={"InstitutionName": "Test Hospital", "AccessionNumber": "ACC001"},
    )
    instance_id = parse_result.indexed[0].instance_id
    deidentify_instance(pipeline_session, bronze_minio_client, settings, instance_id)

    audit_rows = (
        pipeline_session.execute(select(DeidAudit).where(DeidAudit.instance_id == instance_id))
        .scalars()
        .all()
    )

    actions_by_tag_name = {r.tag_name: r.action for r in audit_rows}
    assert actions_by_tag_name.get("PatientName") == "replace"
    assert actions_by_tag_name.get("InstitutionName") == "remove"
    assert actions_by_tag_name.get("AccessionNumber") == "replace"
    assert actions_by_tag_name.get("StudyInstanceUID") == "uid_remap"
    assert actions_by_tag_name.get("SeriesInstanceUID") == "uid_remap"
    assert actions_by_tag_name.get("SOPInstanceUID") == "uid_remap"
    # A pixel-channel row is always recorded, proving the OCR scan ran even with no text
    # found (DICOM.md: "Redact, then set the tag honestly").
    assert any(r.channel == "pixel" for r in audit_rows)


@integration
@ocr
def test_s4_deidentified_dataset_has_prohibited_tags_cleared(
    pipeline_session, bronze_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session,
        bronze_minio_client,
        tmp_path,
        extra_tags={"InstitutionName": "Test Hospital"},
    )
    instance_id = parse_result.indexed[0].instance_id
    result = deidentify_instance(pipeline_session, bronze_minio_client, settings, instance_id)

    ds = result.dataset
    assert "InstitutionName" not in ds
    assert str(ds.PatientName) in ("", "None")
    assert str(ds.PatientID) in ("", "None")
    assert ds.BurnedInAnnotation == "NO"


@integration
@ocr
def test_s4_uid_remap_is_deterministic_across_instances_in_same_series(
    pipeline_session, bronze_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session, bronze_minio_client, tmp_path
    )

    results = [
        deidentify_instance(pipeline_session, bronze_minio_client, settings, r.instance_id)
        for r in parse_result.indexed
    ]
    study_uids = {str(r.dataset.StudyInstanceUID) for r in results}
    series_uids = {str(r.dataset.SeriesInstanceUID) for r in results}
    # All three instances share one study/series -- the pseudonym must be the same value
    # every time (deterministic HMAC), not independently randomized per instance.
    assert len(study_uids) == 1
    assert len(series_uids) == 1
