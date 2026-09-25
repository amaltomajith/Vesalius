from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SeriesSegmentResult(BaseModel):
    series_id: UUID
    preset: str
    skipped: bool = False
    skip_reason: str | None = None
    gate_reason_code: str | None = None  # the DefacingGateError.reason_code, when skipped for it
    quarantined: bool = False
    reason_code: str | None = None
    mask_object_key: str | None = None
    report_object_key: str | None = None
    final_voxels: int | None = None
    coverage_fraction: float | None = None


class BatchSegmentResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesSegmentResult]
    segmented_count: int
