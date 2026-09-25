from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class IngestRequest(BaseModel):
    source: str
    path: str


class IngestFileResult(BaseModel):
    name: str
    content_hash: str
    instance_subject_id: UUID
    bronze_key: str
    duplicate: bool


class IngestBatchResult(BaseModel):
    batch_id: UUID
    source: str
    file_count: int
    bytes_total: int
    files: list[IngestFileResult]
    non_dicom_skipped: int
