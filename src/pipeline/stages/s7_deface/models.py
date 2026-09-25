from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class SeriesDefaceResult(BaseModel):
    """Everything here is reconstructable from the catalog, so a re-run over already-defaced
    series returns a result equal to the first run's."""

    series_id: UUID
    skipped: bool = False
    skip_reason: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    required: bool | None = None
    applied: bool | None = None
    method: str | None = None
    reason: str | None = None  # the decision reason -- never empty once a decision was made
    defaced_object_key: str | None = None
    report_object_key: str | None = None


class BatchDefaceResult(BaseModel):
    batch_id: UUID
    series_results: list[SeriesDefaceResult]
    defaced_count: int  # required and applied
    not_required_count: int
