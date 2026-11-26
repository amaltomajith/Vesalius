"""Catalog lookups shared by the structures-mode stages (S9-S11).

Each downstream stage reads its upstream's structures-mode output through these, never through
the upstream stage's service module (the static architecture guard forbids that). Every silver
read is re-hashed against the catalog row: a changed file is refused, never used.
"""

from __future__ import annotations

from uuid import UUID

from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.models import Artifact, TransformRun
from pipeline.common.config import Settings
from pipeline.common.errors import StructuresInputTamperedError
from pipeline.common.hashing import sha256_of_bytes
from pipeline.storage.object_store import get_bytes


def latest_structures_artifacts(
    session: Session, stage: str, series_id: UUID, kinds: tuple[str, ...]
) -> tuple[TransformRun, dict[str, Artifact]] | None:
    """The latest successful structures-mode run of `stage` for this series that has all
    `kinds` catalogued, with its artifacts by kind."""
    runs = session.execute(
        select(TransformRun)
        .where(
            TransformRun.stage == stage,
            TransformRun.subject_id == series_id,
            TransformRun.status == "success",
            TransformRun.parameters["algorithm"]["mode"].astext == "structures",
        )
        .order_by(TransformRun.completed_at.desc())
    ).scalars()
    for run in runs:
        arts = {
            a.kind: a
            for a in session.execute(
                select(Artifact).where(Artifact.produced_by_run == run.id)
            ).scalars()
        }
        if all(k in arts for k in kinds):
            return run, arts
    return None


def artifacts_of_run(session: Session, run_id: UUID) -> dict[str, Artifact]:
    return {
        a.kind: a
        for a in session.execute(
            select(Artifact).where(Artifact.produced_by_run == run_id)
        ).scalars()
    }


def read_verified(silver: Minio, settings: Settings, art: Artifact, *, stage: str) -> bytes:
    data = get_bytes(silver, settings.minio_silver_bucket, art.object_key)
    if sha256_of_bytes(data) != art.sha256:
        raise StructuresInputTamperedError(stage, {"object_key": art.object_key, "kind": art.kind})
    return data
