from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class VerifyFailure:
    reason_code: str
    detail: dict[str, object]


@dataclass
class VerifyResult:
    instance_id: UUID
    passed: bool
    reason_codes: list[str]
    object_key: str | None = None
