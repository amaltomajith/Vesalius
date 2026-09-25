"""S5 -- Verify. See refs/STAGES.md S5.

Independent of S4 (see rescan.py/pixel_verify.py's module docstrings). Re-reads the
ORIGINAL bronze bytes fresh rather than trusting restricted.uid_map (which S4 wrote) --
verifying against S4's own audit trail would validate against S4's possibly-wrong
assumption, exactly the "a de-identifier that verifies itself proves nothing" failure mode.

On any failure, nothing is written to silver at all (STAGES.md: "do not warn and
continue"). On full pass, this is the stage that actually performs the silver write --
CLAUDE.md's "S5 gates S6, unverified de-identification never reaches silver" only parses as
a real gate if S5 itself controls the write.
"""

from __future__ import annotations

import io
from uuid import UUID, uuid4

import pydicom
from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import StageContext, StageOutcome, run_stage
from pipeline.catalog.models import Artifact, Instance, Quarantine
from pipeline.common.config import Settings
from pipeline.common.errors import QuarantineError
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s4_deidentify.models import DeidentifyResult
from pipeline.stages.s5_verify.models import VerifyFailure, VerifyResult
from pipeline.stages.s5_verify.pixel_verify import OCR_VERIFY_PROFILE_VERSION, check_pixel_ocr_clean
from pipeline.stages.s5_verify.rescan import (
    check_burned_in_annotation,
    check_no_private_tags_outside_whitelist,
    check_prohibited_tags_absent,
    check_uids_differ_from_original,
)
from pipeline.storage.bronze import bronze_key
from pipeline.storage.object_store import put_bytes

STAGE = "S5"
STAGE_VERSION = "1.0.0"
VERIFY_PROFILE_VERSION = "1"


def s5_params() -> dict[str, str]:
    """The exact `transform_runs.parameters` shape S5 records -- version identifiers
    only, never a secret value. S5 doesn't even read the de-id secrets directly (it
    re-derives nothing HMAC-based itself), but this mirrors s4_deidentify.service.s4_params
    as the single place this contract is defined and directly testable.
    """
    return {
        "verify_profile_version": VERIFY_PROFILE_VERSION,
        "ocr_verify_profile_version": OCR_VERIFY_PROFILE_VERSION,
    }


def _read_bronze_bytes(bronze_client: Minio, settings: Settings, content_hash: str) -> bytes:
    key = bronze_key(content_hash)
    response = bronze_client.get_object(settings.minio_bronze_bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _run_all_checks(
    ds: pydicom.dataset.Dataset, original_ds: pydicom.dataset.Dataset
) -> list[VerifyFailure]:
    checks = (
        check_prohibited_tags_absent(ds),
        check_no_private_tags_outside_whitelist(ds),
        check_uids_differ_from_original(ds, original_ds),
        check_burned_in_annotation(ds),
        check_pixel_ocr_clean(ds),
    )
    return [f for f in checks if f is not None]


def _serialize(ds: pydicom.dataset.Dataset) -> bytes:
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def _silver_instance_key(series_id: UUID, pseudo_sop_uid: str) -> str:
    return f"{series_id}/instances/{pseudo_sop_uid}.dcm"


def verify_and_promote_instance(
    session: Session,
    bronze_client: Minio,
    silver_client: Minio,
    settings: Settings,
    instance_id: UUID,
    deid_result: DeidentifyResult,
) -> VerifyResult:
    instance = session.get(Instance, instance_id)
    if instance is None:
        raise ValueError(f"no instance with id {instance_id}")

    original_bytes = _read_bronze_bytes(bronze_client, settings, instance.content_hash)
    original_ds = pydicom.dcmread(io.BytesIO(original_bytes))

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="instance",
        subject_id=instance_id,
        input_hashes=[instance.content_hash],
        params=s5_params(),
    )

    failure_result: VerifyResult | None = None
    serialized_holder: list[bytes] = []
    object_key_holder: list[str] = []

    def _do_verify_and_write() -> StageOutcome:
        nonlocal failure_result
        failures = _run_all_checks(deid_result.dataset, original_ds)
        if failures:
            for f in failures:
                session.add(
                    Quarantine(
                        id=uuid4(),
                        subject_type="instance",
                        subject_id=instance_id,
                        stage=STAGE,
                        reason_code=f.reason_code,
                        detail=f.detail,
                    )
                )
            session.flush()
            failure_result = VerifyResult(
                instance_id=instance_id,
                passed=False,
                reason_codes=[f.reason_code for f in failures],
            )
            # Any failure -> nothing written to silver. Raise so run_stage records this
            # transform_run as failed (a verification failure IS an execution outcome
            # worth failing the run over, unlike S3's "quarantined is still a successful
            # run" -- S5 either produces a trustworthy silver artifact or it does not).
            raise QuarantineError(
                failures[0].reason_code, {"all_failures": [f.reason_code for f in failures]}
            )

        serialized = _serialize(deid_result.dataset)
        object_key = _silver_instance_key(instance.series_id, instance.pseudo_sop_uid)
        put_bytes(
            silver_client, settings.minio_silver_bucket, object_key, serialized, "application/dicom"
        )
        serialized_holder.append(serialized)
        object_key_holder.append(object_key)
        return StageOutcome(output_hashes=[instance.content_hash])

    try:
        run, executed = run_stage(session, ctx, _do_verify_and_write)
    except QuarantineError:
        assert failure_result is not None
        return failure_result

    if not executed:
        artifact = session.execute(
            select(Artifact).where(Artifact.produced_by_run == run.id)
        ).scalar_one_or_none()
        if artifact is None:
            raise AssertionError(
                f"S5 transform_run {run.id} succeeded previously but no Artifact row "
                "exists -- idempotency invariant violated"
            )
        return VerifyResult(
            instance_id=instance_id, passed=True, reason_codes=[], object_key=artifact.object_key
        )

    serialized = serialized_holder[0]
    object_key = object_key_holder[0]
    session.add(
        Artifact(
            id=uuid4(),
            series_id=instance.series_id,
            kind="deid_instance_dicom",
            format="dicom",
            zone="silver",
            object_key=object_key,
            sha256=sha256_of_bytes(serialized),
            bytes=len(serialized),
            produced_by_run=run.id,
        )
    )
    session.flush()

    return VerifyResult(
        instance_id=instance_id, passed=True, reason_codes=[], object_key=object_key
    )
