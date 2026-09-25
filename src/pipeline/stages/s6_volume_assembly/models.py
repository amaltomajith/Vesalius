from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SeriesVolumeResult(BaseModel):
    series_id: UUID
    skipped: bool = False
    skip_reason: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    volume_object_key: str | None = None
    meta_object_key: str | None = None
    shape: tuple[int, int, int] | None = None


class BatchVolumeResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesVolumeResult]
    assembled_count: int
