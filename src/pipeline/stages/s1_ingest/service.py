"""S1 -- Ingest. See refs/STAGES.md S1 and refs/DATA-MODEL.md.

S1 does NOT insert into `instances` (its `series_id` is NOT NULL, but series grouping
only happens in S2). S1's audit trail uses
`subject_id = uuid5(NAMESPACE_BRONZE, content_hash)`; S2 later creates the real
`instances` row with the identical id.

Duplicate content across batches is detected explicitly (querying for a prior successful
S1 run for the same subject_id, regardless of which batch produced it) rather than
relying solely on idempotency-key reuse, because each batch's *encounter* with a piece of
content is its own auditable event (`ingest_batch_id` is part of this call's params, so
its idempotency_key is distinct from the original successful run's).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import StageContext, StageOutcome, record_skip, run_stage
from pipeline.catalog.models import IngestBatch, Quarantine, TransformRun
from pipeline.common.config import Settings
from pipeline.common.errors import QuarantineError, ZeroDicomFoundError
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.archive import ArchiveEntry, is_dicom, iter_source
from pipeline.stages.s1_ingest.models import IngestBatchResult, IngestFileResult
from pipeline.storage.bronze import bronze_key, write_once
from pipeline.version import PIPELINE_VERSION

STAGE = "S1"
STAGE_VERSION = "1.0.0"


def _prior_success_exists(session: Session, subject_id: UUID, stage: str) -> bool:
    stmt = (
        select(TransformRun.id)
        .where(
            TransformRun.subject_id == subject_id,
            TransformRun.stage == stage,
            TransformRun.status == "success",
        )
        .limit(1)
    )
    return session.execute(stmt).first() is not None


def _process_entry(
    session: Session,
    minio_client: Minio,
    settings: Settings,
    batch_id: UUID,
    entry: ArchiveEntry,
) -> IngestFileResult:
    content_hash = sha256_of_bytes(entry.data)
    subject_id = instance_id_for_content_hash(content_hash)
    duplicate = _prior_success_exists(session, subject_id, STAGE)

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="instance",
        subject_id=subject_id,
        input_hashes=[content_hash],
        # source_name distinguishes two byte-identical files landing in the SAME batch
        # under different names -- without it, both would compute the identical
        # idempotency_key (same stage/version/input_hashes/ingest_batch_id), and the
        # second file's record_skip() insert would collide with the first file's
        # already-flushed success row on the UNIQUE constraint. ingest_batch_id alone
        # only disambiguates *across* batches, per this module's docstring.
        params={"ingest_batch_id": str(batch_id), "source_name": entry.name},
    )

    if duplicate:
        record_skip(session, ctx, reason="duplicate_content")
    else:

        def _write() -> StageOutcome:
            write_once(minio_client, settings, content_hash, entry.data)
            return StageOutcome(output_hashes=[content_hash])

        run_stage(session, ctx, _write)

    return IngestFileResult(
        name=entry.name,
        content_hash=content_hash,
        instance_subject_id=subject_id,
        bronze_key=bronze_key(content_hash),
        duplicate=duplicate,
    )


def ingest(
    session: Session,
    minio_client: Minio,
    settings: Settings,
    source: str,
    path: str,
) -> IngestBatchResult:
    batch_id = uuid4()
    batch = IngestBatch(
        id=batch_id,
        source=source,
        file_count=0,
        bytes_total=0,
        pipeline_version=PIPELINE_VERSION,
        started_at=datetime.now(UTC),
    )
    session.add(batch)
    session.flush()

    batch_ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="batch",
        subject_id=batch_id,
        input_hashes=[],
        params={"source": source, "path": path},
    )

    files: list[IngestFileResult] = []
    counters = {"skipped": 0, "dicom_found": 0, "bytes_total": 0}

    def _process() -> StageOutcome:
        for entry in iter_source(path):
            if not is_dicom(entry.data):
                counters["skipped"] += 1
                continue
            counters["dicom_found"] += 1
            counters["bytes_total"] += len(entry.data)
            files.append(_process_entry(session, minio_client, settings, batch_id, entry))
        if counters["dicom_found"] == 0:
            raise ZeroDicomFoundError({"path": path, "non_dicom_skipped": counters["skipped"]})
        return StageOutcome()

    try:
        run_stage(session, batch_ctx, _process)
    except QuarantineError as exc:
        session.add(
            Quarantine(
                id=uuid4(),
                subject_type="batch",
                subject_id=batch_id,
                stage=STAGE,
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        )
        session.flush()
        raise

    batch.file_count = len(files)
    batch.bytes_total = counters["bytes_total"]
    batch.completed_at = datetime.now(UTC)
    session.flush()

    return IngestBatchResult(
        batch_id=batch_id,
        source=source,
        file_count=len(files),
        bytes_total=counters["bytes_total"],
        files=files,
        non_dicom_skipped=counters["skipped"],
    )
