from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SeriesSurfaceResult(BaseModel):
    series_id: UUID
    preset: str
    skipped: bool = False
    skip_reason: str | None = None
    gate_reason_code: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    mesh_object_key: str | None = None
    report_object_key: str | None = None
    vertex_count: int | None = None
    face_count: int | None = None


class BatchSurfaceResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesSurfaceResult]
    extracted_count: int
