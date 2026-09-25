"""The transform_runs idempotency mechanism every stage (S1-S12) is built on.

`key = sha256(input_hashes + stage_name + stage_version + params)` (DATA-MODEL.md /
STAGES.md). If a transform_run with that key already succeeded, the work is skipped and
the existing row is reused. A stage must never partially write: `run_stage` always records
success or failure (never leaves a row stuck at 'running' on the happy/unhappy path); a
crash mid-`fn()` is the one gap that leaves a 'running' row for manual inspection, which is
an accepted, documented limit of this mechanism, not a silent partial write.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.lineage import record_edges
from pipeline.catalog.models import TransformRun
from pipeline.common.errors import QuarantineError

T = TypeVar("T")


@dataclass(frozen=True)
class StageContext:
    stage: str
    stage_version: str
    subject_type: str
    subject_id: UUID
    input_hashes: list[str]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class StageOutcome:
    output_hashes: list[str] | None = None


def idempotency_key(ctx: StageContext) -> str:
    payload = {
        "stage": ctx.stage,
        "stage_version": ctx.stage_version,
        "input_hashes": sorted(ctx.input_hashes),
        "params": ctx.params,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _existing_run(session: Session, key: str) -> TransformRun | None:
    return session.execute(
        select(TransformRun).where(TransformRun.idempotency_key == key)
    ).scalar_one_or_none()


def existing_successful_run(session: Session, ctx: StageContext) -> TransformRun | None:
    """Public lookup for "has this exact idempotency key already succeeded" without
    invoking `run_stage`'s side effects -- used by orchestration that wants to short-circuit
    before even reading a stage's real inputs (e.g. skipping a bronze read entirely when
    S5 already succeeded for this content).
    """
    existing = _existing_run(session, idempotency_key(ctx))
    if existing is not None and existing.status == "success":
        return existing
    return None


def run_stage(
    session: Session,
    ctx: StageContext,
    fn: Callable[[], StageOutcome | None],
) -> tuple[TransformRun, bool]:
    """Executes `fn()` under the idempotency mechanism.

    Returns (transform_run, executed). executed=False means an existing successful run
    was found and reused -- fn() was NOT called. On any exception from fn(), a failed
    transform_run is recorded (reason taken from QuarantineError.reason_code when raised,
    else the exception's type/message) and the exception is re-raised so the caller can
    also route the subject to quarantine.
    """
    key = idempotency_key(ctx)
    existing = _existing_run(session, key)
    if existing is not None and existing.status == "success":
        return existing, False

    started_at = datetime.now(UTC)
    if existing is None:
        run = TransformRun(
            id=uuid4(),
            stage=ctx.stage,
            stage_version=ctx.stage_version,
            idempotency_key=key,
            subject_type=ctx.subject_type,
            subject_id=ctx.subject_id,
            parameters=ctx.params,
            input_hashes=ctx.input_hashes,
            status="running",
            started_at=started_at,
        )
        session.add(run)
    else:
        # A prior attempt with identical inputs failed -- retry in place.
        run = existing
        run.status = "running"
        run.reason = None
        run.started_at = started_at
        run.completed_at = None
    session.flush()

    clock_start = time.monotonic()
    try:
        outcome = fn()
    except QuarantineError as exc:
        _mark_failed(run, exc.reason_code, clock_start)
        session.flush()
        raise
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure must be recorded
        _mark_failed(run, f"{type(exc).__name__}: {exc}", clock_start)
        session.flush()
        raise

    run.status = "success"
    run.completed_at = datetime.now(UTC)
    run.duration_ms = int((time.monotonic() - clock_start) * 1000)
    if outcome is not None:
        run.output_hashes = outcome.output_hashes
    session.flush()
    # lineage edges depend only on this run's inputs, so they can be derived the moment it
    # succeeds -- one hook covers every stage (see catalog/lineage.py)
    record_edges(session, run)
    return run, True


def _mark_failed(run: TransformRun, reason: str, clock_start: float) -> None:
    run.status = "failed"
    run.reason = reason
    run.completed_at = datetime.now(UTC)
    run.duration_ms = int((time.monotonic() - clock_start) * 1000)


def record_skip(session: Session, ctx: StageContext, reason: str) -> TransformRun:
    """Records a transform_run with status='skipped' without invoking any work.

    Used when a stage recognizes, before doing any work, that there is nothing to do --
    e.g. S1 seeing content that already landed in bronze via a different batch.
    `ctx.params` must make this call's idempotency_key distinct from the original
    successful run's (e.g. include the current ingest_batch_id) so this insert does not
    collide with `transform_runs.idempotency_key`'s UNIQUE constraint -- every batch that
    encounters the content gets its own audit row, satisfying "record the reference."

    The key deliberately excludes `subject_id`, so a skip with empty `input_hashes` MUST put
    the subject's identity in `params` (e.g. {"series_id": ...}) or every such subject would
    share one key. Calling this again with an identical context is a no-op that returns the
    existing row, so re-running a stage over a still-skipped subject is safe.
    """
    key = idempotency_key(ctx)
    existing = _existing_run(session, key)
    if existing is not None:
        # Re-running a stage over the same still-skippable subject: the skip is already on
        # record, so reuse it. Inserting again would violate the UNIQUE(idempotency_key).
        return existing
    now = datetime.now(UTC)
    run = TransformRun(
        id=uuid4(),
        stage=ctx.stage,
        stage_version=ctx.stage_version,
        idempotency_key=key,
        subject_type=ctx.subject_type,
        subject_id=ctx.subject_id,
        parameters=ctx.params,
        input_hashes=ctx.input_hashes,
        status="skipped",
        reason=reason,
        duration_ms=0,
        started_at=now,
        completed_at=now,
    )
    session.add(run)
    session.flush()
    return run


def idempotent_stage(
    stage: str, stage_version: str
) -> Callable[[Callable[..., StageOutcome | None]], Callable[..., tuple[TransformRun, bool]]]:
    """Decorator for S3-S12 stage functions so they don't have to re-derive `run_stage`.

    Wraps a function of the form `fn(*args, **kwargs) -> StageOutcome | None`. The caller
    supplies `session`, `subject_type`, `subject_id`, `input_hashes`, and `params` as
    keyword-only arguments; everything else is forwarded to the wrapped function.
    """

    def decorator(
        fn: Callable[..., StageOutcome | None],
    ) -> Callable[..., tuple[TransformRun, bool]]:
        def wrapper(
            *args: Any,
            session: Session,
            subject_type: str,
            subject_id: UUID,
            input_hashes: list[str],
            params: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> tuple[TransformRun, bool]:
            ctx = StageContext(
                stage=stage,
                stage_version=stage_version,
                subject_type=subject_type,
                subject_id=subject_id,
                input_hashes=input_hashes,
                params=params or {},
            )
            return run_stage(session, ctx, lambda: fn(*args, **kwargs))

        return wrapper

    return decorator
