"""S3 -- Quality Gate. See refs/STAGES.md S3 and refs/TESTING.md Part 1.

Re-reads every instance in an indexed series from bronze (S2's stop_before_pixels=True
reads aren't enough -- see geometry.py), runs every QG_* check (not fail-fast), and records
a quality_status verdict plus one Quarantine row per failing check. transform_run.status
reflects whether the gate ran to completion, not the verdict -- 'quarantined' is a valid,
successful outcome of a quality gate, not an execution failure. QuarantineError is reserved
for genuine execution failures (S3UnreadableInstanceError) that are distinct from a check
result.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import StageContext, StageOutcome, run_stage
from pipeline.catalog.models import Instance, Quarantine, Series, TransformRun
from pipeline.common.config import Settings
from pipeline.common.dicom_geometry import derive_slice_spacing
from pipeline.common.errors import QuarantineError
from pipeline.common.hashing import instance_id_for_content_hash
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s2_parse_index.tags import resolve_deface_required
from pipeline.stages.s3_quality_gate.body_part import infer_body_part_from_text
from pipeline.stages.s3_quality_gate.checks import GateFailure, run_all_checks
from pipeline.stages.s3_quality_gate.geometry import InstanceGeometry, gather_instance_geometry
from pipeline.stages.s3_quality_gate.models import QualityGateBatchResult, QualityGateSeriesResult

STAGE = "S3"
STAGE_VERSION = "1.0.0"
# "2": closed body-part vocabulary -- forces re-evaluation of series gated under "1"
# "3": JPEG Lossless / JPEG 2000 pixel data can now be decoded, so series refused under "2" with
# QG_PIXEL_DECODE_FAILED for want of a decoder must be re-evaluated
CHECKS_VERSION = "3"


def _maybe_infer_body_part(series: Series, geoms: list[InstanceGeometry]) -> None:
    """If the study's body part is still unresolved, attempt inference from
    StudyDescription (DICOM.md's inference clause, consolidated into S3 -- see the S3
    plan). The raw description text is read only long enough to match a keyword; it is
    never assigned to any persisted field.
    """
    study = series.study
    if study.body_part_source != "unresolved":
        return
    description = next(
        (g.tags.get("StudyDescription") for g in geoms if g.tags.get("StudyDescription")),
        None,
    )
    inferred = infer_body_part_from_text(description)
    if inferred is None:
        return
    deface_required, _source, _normalized = resolve_deface_required(inferred)
    study.body_part = inferred
    study.body_part_source = "inferred"
    study.deface_required = deface_required


def _evaluate_series(
    session: Session, minio_client: Minio, settings: Settings, series: Series
) -> list[GateFailure]:
    geoms = gather_instance_geometry(minio_client, settings, list(series.instances))

    _maybe_infer_body_part(series, geoms)

    positions = [g.slice_position for g in geoms if g.slice_position is not None]
    spacing = derive_slice_spacing(positions)

    failures = run_all_checks(series, geoms, spacing)

    # Persisted regardless of verdict -- these are auditable, reused by S6 rather than
    # recomputed there (S3 plan Sec.6/Sec.9).
    by_id = {g.instance_id: g for g in geoms}
    for inst in series.instances:
        g = by_id.get(inst.id)
        if g is not None:
            inst.slice_position = g.slice_position
    series.slice_spacing = spacing.slice_spacing
    series.spacing_uniform = spacing.spacing_uniform

    return failures


def _gate_params(series: Series) -> dict[str, object]:
    """The parameters that key this run. A body part stated by a person changes the verdict for
    the very same files, so it is part of the key: without it, re-uploading a scan that was
    refused as unlabelled -- now with its body part stated -- would be answered from the earlier
    refusal. Added ONLY when stated, so every other series keeps the key it always had."""
    params: dict[str, object] = {"checks_version": CHECKS_VERSION}
    if series.study.body_part_source == "declared":
        params["body_part_declared"] = series.study.body_part
    return params


def quality_gate_series(
    session: Session, minio_client: Minio, settings: Settings, series_id: UUID
) -> QualityGateSeriesResult:
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    instance_hashes = sorted(i.content_hash for i in series.instances)
    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=instance_hashes,
        params=_gate_params(series),
    )

    result_holder: dict[str, list[GateFailure]] = {}

    def _do_gate() -> StageOutcome:
        failures = _evaluate_series(session, minio_client, settings, series)
        result_holder["failures"] = failures
        if failures:
            series.quality_status = "quarantined"
            for f in failures:
                session.add(
                    Quarantine(
                        id=uuid4(),
                        subject_type="series",
                        subject_id=series_id,
                        stage=STAGE,
                        reason_code=f.reason_code,
                        detail=f.detail,
                    )
                )
        else:
            series.quality_status = "pass"
        session.flush()
        return StageOutcome(output_hashes=instance_hashes)

    try:
        _run, executed = run_stage(session, ctx, _do_gate)
    except QuarantineError as exc:
        # S3UnreadableInstanceError only -- an execution failure, not a check result.
        session.add(
            Quarantine(
                id=uuid4(),
                subject_type="series",
                subject_id=series_id,
                stage=STAGE,
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        )
        session.flush()
        return QualityGateSeriesResult(
            series_id=series_id,
            study_id=series.study_id,
            quality_status=series.quality_status,
            reason_codes=[exc.reason_code],
        )

    if not executed:
        # Idempotent re-run: a prior successful S3 run for this exact input set already
        # exists, so _do_gate() was never called and result_holder was never populated.
        # Reconstruct from the catalog rather than assume a closure-local variable was
        # set -- this exact bug class (KeyError on an unpopulated result_holder) was
        # already found and fixed once in S2; designed correctly here from the start.
        reason_codes = list(
            session.execute(
                select(Quarantine.reason_code).where(
                    Quarantine.subject_type == "series",
                    Quarantine.subject_id == series_id,
                    Quarantine.stage == STAGE,
                )
            )
            .scalars()
            .all()
        )
        return QualityGateSeriesResult(
            series_id=series_id,
            study_id=series.study_id,
            quality_status=series.quality_status,
            reason_codes=reason_codes,
        )

    failures = result_holder["failures"]
    return QualityGateSeriesResult(
        series_id=series_id,
        study_id=series.study_id,
        quality_status=series.quality_status,
        reason_codes=[f.reason_code for f in failures],
    )


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors s2_parse_index.service._content_hashes_for_batch exactly: resolved via
    S1's transform_runs, not instances.ingest_batch_id (verified never populated by any
    S2 code path -- see the S3 plan). This correctly includes duplicate-content
    encounters from earlier batches, which a column on `instances` could not.
    """
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    rows = session.execute(stmt).all()
    return [row[0][0] for row in rows if row[0]]


def quality_gate_batch(
    session: Session, minio_client: Minio, settings: Settings, batch_id: UUID
) -> QualityGateBatchResult:
    content_hashes = _content_hashes_for_batch(session, batch_id)
    instance_ids = [instance_id_for_content_hash(h) for h in content_hashes]

    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )

    evaluated = [quality_gate_series(session, minio_client, settings, sid) for sid in series_ids]
    quarantined_count = sum(1 for r in evaluated if r.quality_status == "quarantined")
    return QualityGateBatchResult(
        batch_id=batch_id, evaluated=evaluated, quarantined_count=quarantined_count
    )
