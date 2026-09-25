"""Internal working types for S4 -- not CLI-facing result models (those live in
orchestration/deid_pipeline.py), so plain dataclasses rather than Pydantic, matching S3's
`GateFailure`/`InstanceGeometry` precedent for per-instance internal data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import pydicom

Channel = Literal["tag", "private", "pixel"]


@dataclass(frozen=True)
class DeidActionRecord:
    """One row's worth of restricted.deid_audit -- STAGES.md: "this table is how you
    prove removal later," so every distinct action gets its own record rather than an
    aggregate.
    """

    channel: Channel
    action: str  # remove | replace | uid_remap | date_shift | redact
    rule_id: str
    tag: str | None = None  # "(0010,0010)" formatted
    tag_name: str | None = None
    pixel_region: list[int] | None = None


@dataclass
class DeidentifyResult:
    instance_id: UUID
    dataset: pydicom.dataset.Dataset
    audit_records: list[DeidActionRecord]
