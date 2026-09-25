from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SeriesMeshPostprocessResult(BaseModel):
    series_id: UUID
    preset: str
    skipped: bool = False
    skip_reason: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    mesh_object_key: str | None = None
    report_object_key: str | None = None
    input_faces: int | None = None
    final_face_count: int | None = None


class BatchMeshPostprocessResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesMeshPostprocessResult]
    processed_count: int
