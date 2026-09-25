"""Session-scoped reporter for the PHI injection proof (TESTING.md Proof 1). Prints the
"N of N ... across M modalities" number at session teardown, before any individual test's
own hard assertion -- so a documented gap is visible even if the suite as a whole fails.
Never weaken the assertion to make this look better (CLAUDE.md rule 8); this fixture exists
to make misses visible, not to hide them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest


@dataclass
class _PhiReport:
    rows: list[dict[str, object]] = field(default_factory=list)

    def record(self, *, modality: str, total: int, removed: int, misses: list[str]) -> None:
        self.rows.append(
            {"modality": modality, "total": total, "removed": removed, "misses": misses}
        )


@pytest.fixture(scope="session")
def phi_report():  # type: ignore[no-untyped-def]
    report = _PhiReport()
    yield report

    if not report.rows:
        return
    total = sum(r["total"] for r in report.rows)  # type: ignore[misc]
    removed = sum(r["removed"] for r in report.rows)  # type: ignore[misc]
    modalities = sorted({r["modality"] for r in report.rows})
    print(
        f"\nPHI injection proof: {removed} of {total} injected identifiers removed "
        f"across {len(modalities)} modalities ({modalities})"
    )
    for row in report.rows:
        if row["misses"]:
            print(f"  MISSES ({row['modality']}): {row['misses']}")
