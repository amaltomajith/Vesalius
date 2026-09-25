"""S5 always runs its own OCR pass (pixel_verify.py), so every test here needs @ocr + the
`ocr_capability` fixture in addition to @integration -- not just Postgres/MinIO.

The "independence" tests deliberately do NOT call s4_deidentify.deidentify_instance --
they hand-build a "pretend already de-identified" Dataset directly and feed it to
verify_and_promote_instance via a manually constructed DeidentifyResult. This is the point:
proving S5's own checks catch a defect regardless of how (or whether) S4 produced the
dataset, not merely that S4-then-S5 happens to work together.
"""

from __future__ import annotations

from pathlib import Path

import pydicom
from pydicom.uid import generate_uid
from sqlalchemy import select

from pipeline.catalog.models import Artifact, Series
from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s4_deidentify.models import DeidentifyResult
from pipeline.stages.s5_verify.service import verify_and_promote_instance
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_clean_ct_series
from tests.fixtures.shared import integration, ocr


def _ingest_clean_passed_series(session, bronze_minio_client, tmp_path: Path, **kwargs):
    settings = get_settings()
    # count=3, not 1: S3's QG_TOO_FEW_SLICES requires >= 3 instances for CT/MR, so a
    # single-instance series never reaches quality_status='pass' at all.
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


def _pretend_deidentified_dataset(original_bytes: bytes, **overrides) -> pydicom.dataset.Dataset:
    """A hand-built "already de-identified" dataset: different UIDs, PHI tags cleared,
    BurnedInAnnotation=NO, valid clean pixel data -- everything S5's checks look for,
    built independently of any S4 code path. `overrides` lets a test re-introduce exactly
    one defect on top of this otherwise-clean baseline.
    """
    import io

    ds = pydicom.dcmread(io.BytesIO(original_bytes))
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    if "FrameOfReferenceUID" in ds:
        ds.FrameOfReferenceUID = generate_uid()
    ds.PatientName = ""
    ds.PatientID = ""
    ds.PatientBirthDate = ""
    if "InstitutionName" in ds:
        del ds.InstitutionName
    ds.BurnedInAnnotation = "NO"
    for name, value in overrides.items():
        setattr(ds, name, value)
    return ds


@integration
@ocr
def test_s5_independent_rescan_catches_a_still_present_prohibited_tag(
    pipeline_session, bronze_minio_client, silver_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session, bronze_minio_client, tmp_path
    )
    instance = parse_result.indexed[0]

    from pipeline.storage.bronze import bronze_key

    response = bronze_minio_client.get_object(
        settings.minio_bronze_bucket, bronze_key(instance.content_hash)
    )
    original_bytes = response.read()
    response.close()
    response.release_conn()

    # Otherwise-clean, EXCEPT PatientName was never actually cleared -- exactly the class
    # of bug an S4-trusting verifier would miss.
    leaky_ds = _pretend_deidentified_dataset(original_bytes, PatientName="LEAKED^NAME")
    fake_deid_result = DeidentifyResult(
        instance_id=instance.instance_id, dataset=leaky_ds, audit_records=[]
    )

    result = verify_and_promote_instance(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        instance.instance_id,
        fake_deid_result,
    )

    assert result.passed is False
    assert "S5_PROHIBITED_TAG_PRESENT" in result.reason_codes

    artifact_count = (
        pipeline_session.execute(select(Artifact).where(Artifact.series_id == series.id))
        .scalars()
        .all()
    )
    assert artifact_count == []  # nothing written to silver on failure


@integration
@ocr
def test_s5_writes_silver_artifact_on_full_pass(
    pipeline_session, bronze_minio_client, silver_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session, bronze_minio_client, tmp_path
    )
    instance = parse_result.indexed[0]

    from pipeline.storage.bronze import bronze_key

    response = bronze_minio_client.get_object(
        settings.minio_bronze_bucket, bronze_key(instance.content_hash)
    )
    original_bytes = response.read()
    response.close()
    response.release_conn()

    clean_ds = _pretend_deidentified_dataset(original_bytes)
    fake_deid_result = DeidentifyResult(
        instance_id=instance.instance_id, dataset=clean_ds, audit_records=[]
    )

    result = verify_and_promote_instance(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        instance.instance_id,
        fake_deid_result,
    )

    assert result.passed is True
    assert result.object_key is not None

    artifacts = (
        pipeline_session.execute(select(Artifact).where(Artifact.series_id == series.id))
        .scalars()
        .all()
    )
    assert len(artifacts) == 1
    assert artifacts[0].kind == "deid_instance_dicom"
    assert artifacts[0].zone == "silver"
    assert artifacts[0].object_key == result.object_key

    # Actually present in MinIO, not just recorded in the catalog.
    stat = silver_minio_client.stat_object(settings.minio_silver_bucket, result.object_key)
    assert stat.size == artifacts[0].bytes


@integration
@ocr
def test_s5_detects_unremapped_uid(
    pipeline_session, bronze_minio_client, silver_minio_client, ocr_capability, tmp_path: Path
) -> None:
    settings = get_settings()
    series, parse_result = _ingest_clean_passed_series(
        pipeline_session, bronze_minio_client, tmp_path
    )
    instance = parse_result.indexed[0]

    from pipeline.storage.bronze import bronze_key

    response = bronze_minio_client.get_object(
        settings.minio_bronze_bucket, bronze_key(instance.content_hash)
    )
    original_bytes = response.read()
    response.close()
    response.release_conn()
    original_ds = pydicom.dcmread(__import__("io").BytesIO(original_bytes))

    ds = _pretend_deidentified_dataset(original_bytes)
    ds.StudyInstanceUID = original_ds.StudyInstanceUID  # "forgot" to remap this one

    fake_deid_result = DeidentifyResult(
        instance_id=instance.instance_id, dataset=ds, audit_records=[]
    )
    result = verify_and_promote_instance(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        settings,
        instance.instance_id,
        fake_deid_result,
    )

    assert result.passed is False
    assert "S5_UID_NOT_REMAPPED" in result.reason_codes
