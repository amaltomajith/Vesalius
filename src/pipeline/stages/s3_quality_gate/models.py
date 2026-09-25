from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class QualityGateSeriesResult(BaseModel):
    series_id: UUID
    study_id: UUID
    quality_status: str
    reason_codes: list[str]


class QualityGateBatchResult(BaseModel):
    batch_id: UUID
    evaluated: list[QualityGateSeriesResult]
    quarantined_count: int
