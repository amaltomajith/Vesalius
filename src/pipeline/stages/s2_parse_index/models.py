from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class ParsedInstanceResult(BaseModel):
    content_hash: str
    instance_id: UUID
    series_id: UUID | None
    study_id: UUID | None
    quarantined: bool
    reason_code: str | None = None


class ParseIndexBatchResult(BaseModel):
    batch_id: UUID
    indexed: list[ParsedInstanceResult]
    quarantined_count: int
