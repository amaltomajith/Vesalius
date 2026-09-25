"""S12 response models. Every model that carries catalog data has a REQUIRED `contract_version`
(STAGES.md S12: "returned in every response that carries data") -- required, not optional, so a
response that forgets it fails validation instead of shipping without it.

Deliberately absent: `pseudo_patient_id`, study dates, `object_key`, content hashes of bronze
instances, `quarantine.detail` -- nothing here is needed by a listed endpoint.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SeriesSummary(BaseModel):
    id: UUID
    pseudo_series_uid: str
    modality: str
    body_part: str | None
    quality_status: str
    instance_count: int
    has_gold: bool


class SeriesListResponse(BaseModel):
    contract_version: str
    total: int
    limit: int
    offset: int
    items: list[SeriesSummary]


class SeriesDetail(BaseModel):
    contract_version: str
    id: UUID
    pseudo_series_uid: str
    modality: str
    body_part: str | None
    deface_required: bool
    quality_status: str
    instance_count: int
    rows: int | None
    columns: int | None
    pixel_spacing: list[float] | None
    slice_spacing: float | None
    spacing_uniform: bool | None
    has_gold: bool


class ArtifactOut(BaseModel):
    id: UUID
    series_id: UUID
    kind: str
    format: str
    sha256: str
    bytes: int
    contract_version: str | None
    created_at: datetime


class ArtifactListResponse(BaseModel):
    contract_version: str
    series_id: UUID
    artifacts: list[ArtifactOut]


class ArtifactDownload(BaseModel):
    contract_version: str
    artifact: ArtifactOut
    url: str
    expires_in_seconds: int


class LineageNodeOut(BaseModel):
    run_id: UUID
    stage: str
    stage_version: str
    status: str
    depth: int
    parameters: dict[str, Any]


class LineageEdgeOut(BaseModel):
    downstream_run: UUID
    upstream_run: UUID


class LineageResponse(BaseModel):
    contract_version: str
    series_id: UUID
    root_runs: list[UUID]
    nodes: list[LineageNodeOut]
    edges: list[LineageEdgeOut]


class StageSummary(BaseModel):
    stage: str
    run_count: int
    by_status: dict[str, int]
    latest_status: str
    latest_reason: str | None


class QuarantineEntry(BaseModel):
    stage: str
    reason_code: str
    created_at: datetime


class QualityReport(BaseModel):
    contract_version: str
    series_id: UUID
    quality_status: str
    instance_count: int
    rows: int | None
    columns: int | None
    pixel_spacing: list[float] | None
    slice_spacing: float | None
    spacing_uniform: bool | None
    stages: list[StageSummary]
    quarantine: list[QuarantineEntry]


class ContractResponse(BaseModel):
    # serialised as "schema" (BaseModel already owns a deprecated `.schema()` method)
    model_config = ConfigDict(populate_by_name=True)

    contract_version: str
    name: str
    version: str
    schema_: dict[str, Any] = Field(alias="schema")
    published_at: datetime
