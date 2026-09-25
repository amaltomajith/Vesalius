"""S4 -- De-identify. See refs/STAGES.md S4 and refs/DICOM.md Part 1.

S4's real output (the redacted, tag-rewritten in-memory Dataset) is never persisted on its
own -- S5 is the only thing that persists a de-identified DICOM, on pass (see
s5_verify/service.py). This means the tag profile / private-tag / pixel channels are
computed OUTSIDE run_stage's closure, on every call, regardless of whether the run turns
out to be an idempotent skip: there is no cheap DB-backed way to reconstruct a full
pixel-redacted Dataset, so unlike S2/S3 there is no closure-local result to lose on a skip
in the first place. `run_stage`'s skip here means "don't double-insert deid_audit rows /
don't re-finalize uid_map a second time" -- the recompute itself is cheap and deterministic.
"""

from __future__ import annotations

import io
from uuid import UUID

import pydicom
from minio import Minio
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import StageContext, StageOutcome, run_stage
from pipeline.catalog.models import DeidAudit, Instance, PatientMap, Study, UidMap
from pipeline.common.config import Settings
from pipeline.common.deid_crypto import patient_offset_days, pseudo_patient_id, pseudo_uid
from pipeline.common.deid_profile import PROFILE_VERSION
from pipeline.common.errors import (
    LowConfidenceBurnedInTextError,
    QuarantineError,
    S4UnreadableInstanceError,
)
from pipeline.stages.s4_deidentify.models import DeidActionRecord, DeidentifyResult
from pipeline.stages.s4_deidentify.pixel_redaction import (
    OCR_REDACTION_PROFILE_VERSION,
    apply_burned_in_redaction,
)
from pipeline.stages.s4_deidentify.private_tags import apply_private_tag_removal
from pipeline.stages.s4_deidentify.rules import apply_tag_profile
from pipeline.storage.bronze import bronze_key

STAGE = "S4"
STAGE_VERSION = "1.0.0"


def s4_params() -> dict[str, str]:
    """The exact `transform_runs.parameters` shape S4 records -- version identifiers
    only, NEVER the secret values themselves (that column is `serving_ro`-readable).
    Pulled out as its own pure function so this contract is directly, cheaply testable
    without any DB/bronze/secret dependency (see tests/unit/test_deid_rules.py).
    """
    return {
        "profile_version": PROFILE_VERSION,
        "ocr_redaction_profile_version": OCR_REDACTION_PROFILE_VERSION,
    }


def _require_secrets(settings: Settings) -> None:
    # Hard config error, not a QuarantineError: HMAC with an empty key is a silent
    # security foot-gun, not a per-instance data problem. No earlier stage needed this
    # guard since none of them read these values.
    if not settings.deid_uid_secret or not settings.deid_date_shift_secret:
        raise RuntimeError(
            "DEID_UID_SECRET and DEID_DATE_SHIFT_SECRET must both be set (non-empty) "
            "before S4 can run -- see .env.example."
        )


def _read_bronze_bytes(minio_client: Minio, settings: Settings, content_hash: str) -> bytes:
    key = bronze_key(content_hash)
    response = minio_client.get_object(settings.minio_bronze_bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _apply_all_channels(
    ds: pydicom.dataset.Dataset, settings: Settings, *, date_offset_days: int
) -> list[DeidActionRecord]:
    records: list[DeidActionRecord] = []
    records += apply_tag_profile(
        ds, uid_secret=settings.deid_uid_secret, date_offset_days=date_offset_days
    )
    records += apply_private_tag_removal(ds)

    pixel_records, needs_review = apply_burned_in_redaction(ds)
    if needs_review:
        raise LowConfidenceBurnedInTextError(
            {"reason": "OCR detected possible text at ambiguous confidence -- needs human review"}
        )
    records += pixel_records
    return records


def _finalize_uid_map(session: Session, original_uid: str, uid_type: str, secret: str) -> str:
    real = pseudo_uid(original_uid, secret)
    row = session.get(UidMap, original_uid)
    if row is None:
        # Not pre-registered by S2 (e.g. InstanceCreatorUID, which S2 never captures) --
        # first registration here, no provisional phase needed since S4 computes the real
        # value directly.
        row = UidMap(original_uid=original_uid, pseudo_uid=real, uid_type=uid_type)
        session.add(row)
    else:
        row.pseudo_uid = real
    session.flush()
    return real


def _finalize_patient_map(
    session: Session, original_patient_id: str, uid_secret: str, date_secret: str
) -> tuple[str, int]:
    real_pseudo = pseudo_patient_id(original_patient_id, uid_secret)
    offset = patient_offset_days(original_patient_id, date_secret)
    row = session.get(PatientMap, original_patient_id)
    if row is None:
        row = PatientMap(
            original_patient_id=original_patient_id,
            pseudo_patient_id=real_pseudo,
            date_offset_days=offset,
        )
        session.add(row)
    else:
        row.pseudo_patient_id = real_pseudo
        row.date_offset_days = offset
    session.flush()
    return real_pseudo, offset


def deidentify_instance(
    session: Session, bronze_client: Minio, settings: Settings, instance_id: UUID
) -> DeidentifyResult:
    """Assumes the caller (orchestration/deid_pipeline.py) has already guarded that this
    instance's series has quality_status == 'pass' -- this function does not re-check.
    """
    _require_secrets(settings)

    instance = session.get(Instance, instance_id)
    if instance is None:
        raise ValueError(f"no instance with id {instance_id}")

    try:
        original_bytes = _read_bronze_bytes(bronze_client, settings, instance.content_hash)
        ds: pydicom.dataset.Dataset | None = pydicom.dcmread(io.BytesIO(original_bytes))
    except Exception as exc:  # noqa: BLE001 - infra failure, not a de-id outcome
        ds = None
        compute_error: QuarantineError | None = S4UnreadableInstanceError(
            {
                "instance_id": str(instance_id),
                "content_hash": instance.content_hash,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    else:
        compute_error = None

    original_study_uid = original_series_uid = original_sop_uid = ""
    original_frame_of_reference_uid: str | None = None
    original_patient_id: str | None = None
    audit_records: list[DeidActionRecord] = []

    if compute_error is None:
        assert ds is not None
        original_study_uid = str(ds.StudyInstanceUID)
        original_series_uid = str(ds.SeriesInstanceUID)
        original_sop_uid = str(ds.SOPInstanceUID)
        original_frame_of_reference_uid = (
            str(ds.FrameOfReferenceUID) if "FrameOfReferenceUID" in ds else None
        )
        original_patient_id = str(ds.PatientID) if "PatientID" in ds and ds.PatientID else None
        # No patient id to key an offset on (PatientID is optional, rare per S2's own
        # comment) -- fall back to the study UID so the shift is still deterministic, just
        # not shared across a patient's other studies (nothing to share it with here).
        date_key = original_patient_id or original_study_uid
        offset_days = patient_offset_days(date_key, settings.deid_date_shift_secret)

        # Pure, deterministic, no DB writes -- recomputed on every call. This is what
        # makes the idempotent-skip path safe without ever needing to persist the Dataset.
        try:
            audit_records = _apply_all_channels(ds, settings, date_offset_days=offset_days)
        except QuarantineError as exc:
            compute_error = exc

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="instance",
        subject_id=instance_id,
        input_hashes=[instance.content_hash],
        params=s4_params(),
    )

    def _do_persist() -> StageOutcome:
        # Raised here (inside the closure) rather than before run_stage was ever called,
        # so a bronze-read or channel-application failure still gets recorded as a failed
        # transform_run (CLAUDE.md rule 7) instead of escaping before any run row exists.
        if compute_error is not None:
            raise compute_error
        _finalize_uid_map(session, original_study_uid, "study", settings.deid_uid_secret)
        _finalize_uid_map(session, original_series_uid, "series", settings.deid_uid_secret)
        _finalize_uid_map(session, original_sop_uid, "sop", settings.deid_uid_secret)
        if original_frame_of_reference_uid:
            _finalize_uid_map(
                session,
                original_frame_of_reference_uid,
                "frame_of_reference",
                settings.deid_uid_secret,
            )
        if original_patient_id:
            real_pseudo_patient, _offset = _finalize_patient_map(
                session,
                original_patient_id,
                settings.deid_uid_secret,
                settings.deid_date_shift_secret,
            )
            study = session.get(Study, instance.series.study_id)
            if study is not None:
                study.pseudo_patient_id = real_pseudo_patient

        study = session.get(Study, instance.series.study_id)
        if study is not None:
            study.pseudo_study_uid = pseudo_uid(original_study_uid, settings.deid_uid_secret)
        instance.series.pseudo_series_uid = pseudo_uid(
            original_series_uid, settings.deid_uid_secret
        )
        instance.pseudo_sop_uid = pseudo_uid(original_sop_uid, settings.deid_uid_secret)

        for record in audit_records:
            session.add(
                DeidAudit(
                    instance_id=instance_id,
                    channel=record.channel,
                    tag=record.tag,
                    tag_name=record.tag_name,
                    action=record.action,
                    rule_id=record.rule_id,
                    pixel_region=record.pixel_region,
                )
            )
        session.flush()
        return StageOutcome(output_hashes=[instance.content_hash])

    run_stage(session, ctx, _do_persist)

    # Reached only on success (run_stage re-raises on failure), at which point
    # compute_error was None and ds was assigned.
    assert ds is not None
    return DeidentifyResult(instance_id=instance_id, dataset=ds, audit_records=audit_records)
