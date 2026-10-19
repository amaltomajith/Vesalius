"""What the wake function decides, kept free of Azure so it can be tested anywhere.

The static site (GitHub Pages) asks this function to wake the live backend, a VM that is
deallocated whenever nobody uses it (scripts/deploy/autostop.sh). The function only ever reads
the VM's power state and starts it; a daily cap bounds what a stranger can spend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ASLEEP = {"deallocated", "stopped"}
GOING_TO_SLEEP = {"deallocating", "stopping"}


def power_of(status_codes: list[str]) -> str:
    """The VM's power state from its instance view: "PowerState/deallocated" -> "deallocated"."""
    for code in status_codes:
        if code.startswith("PowerState/"):
            return code.split("/", 1)[1]
    return "unknown"


@dataclass(frozen=True)
class Decision:
    start: bool  # call begin_start on the VM
    status: int  # the HTTP status to answer with
    body: dict[str, Any] = field(default_factory=dict)


def decide_wake(power: str, ready: bool, wakes_today: int, cap: int) -> Decision:
    """POST /api/wake: start the VM if it is asleep and today's wakes allow it."""
    left = max(0, cap - wakes_today)
    if power in ASLEEP:
        if left <= 0:
            return Decision(
                False,
                429,
                {
                    "power": power,
                    "ready": False,
                    "wakes_left": 0,
                    "message": "The live pipeline has been woken enough times today. Try tomorrow; "
                    "everything else on the site still works.",
                },
            )
        return Decision(True, 202, {"power": "starting", "ready": False, "wakes_left": left - 1})
    return Decision(False, *_report(power, ready, left))


def decide_status(power: str, ready: bool, wakes_today: int, cap: int) -> Decision:
    """GET /api/status: report only."""
    return Decision(False, *_report(power, ready, max(0, cap - wakes_today)))


def _report(power: str, ready: bool, left: int) -> tuple[int, dict[str, Any]]:
    awake = ready and power == "running"
    body: dict[str, Any] = {"power": power, "ready": awake, "wakes_left": left}
    if power in ASLEEP or power in GOING_TO_SLEEP or power in {"running", "starting"}:
        return 200, body
    body["message"] = "The live pipeline's server is in an unexpected state. Try again later."
    return 503, body


def parse_vm_id(resource_id: str) -> tuple[str, str, str]:
    """(subscription, resource group, VM name) from an ARM resource id."""
    parts = resource_id.strip("/").split("/")
    try:
        sub = parts[parts.index("subscriptions") + 1]
        group = parts[[p.lower() for p in parts].index("resourcegroups") + 1]
        name = parts[parts.index("virtualMachines") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"not a VM resource id: {resource_id!r}") from exc
    return sub, group, name
