"""Orchestrates S4 (de-identify) then S5 (verify) per instance. S4 produces an in-memory
dataset that S5 immediately consumes -- nothing is persisted in between -- but both still
record their own independent transform_runs (each stage's own run_stage() call does this
unconditionally, no special-casing needed here).

Series-quality-status guard lives here, not in s4_deidentify/service.py: STAGES.md's S4
"In: bronze instance + series quality status" plus "everything downstream of a quarantine
stops for that series" (stage dependency graph) means a quarantined series never reaches S4
at all, for any of its instances.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from minio import Minio
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import StageContext, existing_successful_run, record_skip
from pipeline.catalog.models import Artifact, Instance, Quarantine, Series, TransformRun
from pipeline.common.config import Settings
from pipeline.common.errors import QuarantineError
from pipeline.common.hashing import instance_id_for_content_hash
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s4_deidentify.service import STAGE as S4_STAGE
from pipeline.stages.s4_deidentify.service import STAGE_VERSION as S4_STAGE_VERSION
from pipeline.stages.s4_deidentify.service import deidentify_instance
from pipeline.stages.s5_verify.service import STAGE as S5_STAGE
from pipeline.stages.s5_verify.service import STAGE_VERSION as S5_STAGE_VERSION
from pipeline.stages.s5_verify.service import s5_params, verify_and_promote_instance


class InstanceDeidVerifyResult(BaseModel):
    instance_id: UUID
    series_id: UUID
    stage_failed: str | None = None  # "S4" | "S5" | None
    reason_code: str | None = None
    silver_object_key: str | None = None


class SeriesDeidVerifyResult(BaseModel):
    series_id: UUID
    skipped: bool
    results: list[InstanceDeidVerifyResult]


class BatchDeidVerifyResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesDeidVerifyResult]
    quarantined_count: int


def _s5_ctx_for(instance: Instance) -> StageContext:
    return StageContext(
        stage=S5_STAGE,
        stage_version=S5_STAGE_VERSION,
        subject_type="instance",
        subject_id=instance.id,
        input_hashes=[instance.content_hash],
        params=s5_params(),
    )


def deidentify_and_verify_instance(
    session: Session,
    bronze_client: Minio,
    silver_client: Minio,
    settings: Settings,
    instance_id: UUID,
) -> InstanceDeidVerifyResult:
    instance = session.get(Instance, instance_id)
    if instance is None:
        raise ValueError(f"no instance with id {instance_id}")

    # Cheap short-circuit: if S5 already succeeded for this content, skip S4's recompute
    # and S5's re-verification entirely -- don't even read bronze.
    existing_s5 = existing_successful_run(session, _s5_ctx_for(instance))
    if existing_s5 is not None:
        artifact = session.execute(
            select(Artifact).where(Artifact.produced_by_run == existing_s5.id)
        ).scalar_one_or_none()
        if artifact is None:
            raise AssertionError(
                f"S5 transform_run {existing_s5.id} succeeded previously but no Artifact "
                "row exists -- idempotency invariant violated"
            )
        return InstanceDeidVerifyResult(
            instance_id=instance_id,
            series_id=instance.series_id,
            silver_object_key=artifact.object_key,
        )

    try:
        deid_result = deidentify_instance(session, bronze_client, settings, instance_id)
    except QuarantineError as exc:
        session.add(
            Quarantine(
                id=uuid4(),
                subject_type="instance",
                subject_id=instance_id,
                stage="S4",
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        )
        session.flush()
        return InstanceDeidVerifyResult(
            instance_id=instance_id,
            series_id=instance.series_id,
            stage_failed="S4",
            reason_code=exc.reason_code,
        )

    verify_result = verify_and_promote_instance(
        session, bronze_client, silver_client, settings, instance_id, deid_result
    )
    if not verify_result.passed:
        # verify_and_promote_instance already wrote its own Quarantine row(s) -- possibly
        # more than one, since S5 runs every check rather than failing fast (matching S3's
        # precedent), which a single caught exception here could not represent.
        return InstanceDeidVerifyResult(
            instance_id=instance_id,
            series_id=instance.series_id,
            stage_failed="S5",
            reason_code=verify_result.reason_codes[0] if verify_result.reason_codes else None,
        )

    return InstanceDeidVerifyResult(
        instance_id=instance_id,
        series_id=instance.series_id,
        silver_object_key=verify_result.object_key,
    )


def deidentify_and_verify_series(
    session: Session,
    bronze_client: Minio,
    silver_client: Minio,
    settings: Settings,
    series_id: UUID,
) -> SeriesDeidVerifyResult:
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    if series.quality_status != "pass":
        record_skip(
            session,
            StageContext(
                stage=S4_STAGE,
                stage_version=S4_STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={"series_id": str(series_id)},
            ),
            reason="series_not_passed_quality_gate",
        )
        return SeriesDeidVerifyResult(series_id=series_id, skipped=True, results=[])

    results = [
        deidentify_and_verify_instance(session, bronze_client, silver_client, settings, inst.id)
        for inst in series.instances
    ]
    return SeriesDeidVerifyResult(series_id=series_id, skipped=False, results=results)


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors s2_parse_index.service._content_hashes_for_batch and
    s3_quality_gate.service._content_hashes_for_batch exactly: resolved via S1's
    transform_runs, not instances.ingest_batch_id (confirmed never populated).
    """
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    rows = session.execute(stmt).all()
    return [row[0][0] for row in rows if row[0]]


def deidentify_and_verify_batch(
    session: Session, bronze_client: Minio, silver_client: Minio, settings: Settings, batch_id: UUID
) -> BatchDeidVerifyResult:
    content_hashes = _content_hashes_for_batch(session, batch_id)
    instance_ids = [instance_id_for_content_hash(h) for h in content_hashes]

    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )

    series_results = [
        deidentify_and_verify_series(session, bronze_client, silver_client, settings, sid)
        for sid in series_ids
    ]
    quarantined_count = sum(
        1 for sr in series_results for r in sr.results if r.stage_failed is not None
    )
    return BatchDeidVerifyResult(
        batch_id=batch_id, series_results=series_results, quarantined_count=quarantined_count
    )
