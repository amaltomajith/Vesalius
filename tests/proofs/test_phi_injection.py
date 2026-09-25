"""TESTING.md Proof 1 -- PHI injection and removal. Take known-clean public-style DICOM,
inject synthetic PHI across all three channels, run it through the real S1-S5 pipeline, and
assert removal. Reports the removal rate as a measured number ("N of N... across M
modalities"), honestly -- including any misses, per CLAUDE.md rule 8 ("never weaken a test
to make it pass") and TESTING.md's own "a known, documented gap is far better than an
inflated claim."

Only CT is exercised through the full pipeline: S3's QG_MODALITY_UNSUPPORTED check
quarantines any series whose modality isn't CT/MR before it ever reaches S4/S5 (this
project's volumetric pipeline is CT/MR-only, confirmed during S3's implementation) -- so a
US series cannot flow through ingest->...->deidentify_and_verify_batch at all. This is a
real project constraint, not a scope-narrowing choice; testing PHI removal against a
quarantined-before-S4 instance would not exercise S4/S5's channels regardless of modality
label. The burned-in-pixel channel is exercised identically via CT with injected pixel
text, so no channel coverage is lost.

Uses its own small tag-value walker below, deliberately not importing s4_deidentify or
s5_verify internals -- the proof exercises the pipeline's real output artifact (the silver
object), not the stages' own code paths, exactly like a genuine external auditor would.
"""

from __future__ import annotations

import io
from pathlib import Path

import pydicom
from sqlalchemy import select

from pipeline.catalog.models import DeidAudit
from pipeline.common.config import get_settings
from pipeline.common.ocr import detect_text_regions, render_pixel_array_to_image
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_clean_ct_series
from tests.fixtures.shared import integration, ocr

# TESTING.md Proof 1's exact injection list.
TAG_INJECTIONS: list[tuple[str, str]] = [
    ("PatientName", "TESTPHI^ALPHA"),
    ("PatientID", "PHI-000001"),
    ("PatientBirthDate", "19551104"),
    ("InstitutionName", "TESTPHI GENERAL HOSPITAL"),
    ("ReferringPhysicianName", "TESTPHI^BETA"),
    ("StudyDescription", "CT HEAD - TESTPHI^GAMMA"),
    ("PatientComments", "contact 555-0142"),
]
PRIVATE_TAG_INJECTION: tuple[tuple[int, int], str, str] = (
    (0x0009, 0x0010),
    "LO",
    "TESTPHI VENDOR BLOCK",
)
PIXEL_TEXT_INJECTION = "TESTPHI^DELTA MRN 88231"

TOTAL_INJECTIONS = len(TAG_INJECTIONS) + 1 + 1  # tags + private + pixel


def _all_string_values(ds: pydicom.dataset.Dataset) -> list[str]:
    values: list[str] = []
    for elem in ds:
        if elem.VR == "SQ":
            for item in elem.value:
                values.extend(_all_string_values(item))
        else:
            values.append(str(elem.value))
    return values


def _string_absent_everywhere(ds: pydicom.dataset.Dataset, needle: str) -> bool:
    return not any(needle in v for v in _all_string_values(ds))


def _no_private_tags_present(ds: pydicom.dataset.Dataset) -> bool:
    for elem in ds:
        if elem.VR == "SQ":
            for item in elem.value:
                if not _no_private_tags_present(item):
                    return False
        elif elem.tag.is_private:
            return False
    return True


@integration
@ocr
def test_phi_injection_removed_across_all_three_channels(
    pipeline_session,
    bronze_minio_client,
    silver_minio_client,
    ocr_capability,
    phi_report,
    tmp_path: Path,
) -> None:
    settings = get_settings()

    extra_tags = dict(TAG_INJECTIONS)
    private_group, private_vr, private_value = PRIVATE_TAG_INJECTION
    series_bytes = build_clean_ct_series(
        count=3,
        body_part="CHEST",
        # Wide enough that the full injected string actually renders within the canvas
        # (confirmed via a real OCR diagnostic run -- a narrower canvas clipped the tail
        # of the string before OCR ever saw it, which would have made the proof pass by
        # omission rather than by actually exercising redaction of the whole string).
        rows=200,
        columns=600,
        extra_tags=extra_tags,
        private_tags={private_group: (private_vr, private_value)},
        burned_text=PIXEL_TEXT_INJECTION,
    )
    original_ds = pydicom.dcmread(io.BytesIO(series_bytes[0]))
    original_study_uid = str(original_ds.StudyInstanceUID)
    original_series_uid = str(original_ds.SeriesInstanceUID)
    original_sop_uid = str(original_ds.SOPInstanceUID)

    archive = build_zip(
        tmp_path, "phi.zip", {f"scan{i}.dcm": data for i, data in enumerate(series_bytes)}
    )
    batch = ingest(pipeline_session, bronze_minio_client, settings, "pytest", str(archive))
    parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    quality_gate_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)
    result = deidentify_and_verify_batch(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, batch.batch_id
    )

    instance_results = [r for sr in result.series_results for r in sr.results]
    assert instance_results, "no instances were evaluated -- pipeline did not run"
    first = instance_results[0]
    assert first.stage_failed is None, (
        f"instance quarantined at {first.stage_failed}: {first.reason_code} -- cannot "
        "measure removal on a quarantined instance"
    )

    response = silver_minio_client.get_object(settings.minio_silver_bucket, first.silver_object_key)
    silver_bytes = response.read()
    response.close()
    response.release_conn()
    deid_ds = pydicom.dcmread(io.BytesIO(silver_bytes))

    misses: list[str] = []

    for tag_name, injected_value in TAG_INJECTIONS:
        if not _string_absent_everywhere(deid_ds, injected_value):
            misses.append(f"tag:{tag_name}")

    if not _no_private_tags_present(deid_ds):
        misses.append("private:(0009,0010)")

    image = render_pixel_array_to_image(deid_ds.pixel_array)
    detected_text = " ".join(r.text for r in detect_text_regions(image))
    if any(token in detected_text for token in ("TESTPHI", "88231")):
        misses.append("pixel:burned_in_text")

    for original_uid in (original_study_uid, original_series_uid, original_sop_uid):
        if not _string_absent_everywhere(deid_ds, original_uid):
            misses.append(f"uid:{original_uid}")

    audit_rows = (
        pipeline_session.execute(
            select(DeidAudit).where(DeidAudit.instance_id == first.instance_id)
        )
        .scalars()
        .all()
    )
    audit_channels_present = {r.channel for r in audit_rows}
    if "tag" not in audit_channels_present:
        misses.append("deid_audit:no tag-channel rows")
    if "private" not in audit_channels_present:
        misses.append("deid_audit:no private-channel rows")
    if "pixel" not in audit_channels_present:
        misses.append("deid_audit:no pixel-channel rows")

    removed = TOTAL_INJECTIONS - len(
        [m for m in misses if not m.startswith(("uid:", "deid_audit:"))]
    )
    phi_report.record(modality="CT", total=TOTAL_INJECTIONS, removed=removed, misses=misses)

    assert not misses, f"PHI injection misses: {misses}"
