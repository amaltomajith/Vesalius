from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SeriesExportResult(BaseModel):
    series_id: UUID
    preset: str
    skipped: bool = False
    skip_reason: str | None = None
    gate_reason_code: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    glb_object_key: str | None = None
    volume_object_key: str | None = None
    thumbnail_object_key: str | None = None
    manifest_object_key: str | None = None
    face_count: int | None = None


class BatchExportResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesExportResult]
    exported_count: int
