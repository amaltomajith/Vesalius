"""deploy/azure/wake/logic.py: when the static site may start the live backend's VM.

The wake function is reachable by anyone, so it is the one place a stranger could spend the
student credit: it may only start a VM that is asleep, at most the daily cap, and must never
report the backend ready before the VM is running and answering.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_path = Path(__file__).parents[2] / "deploy" / "azure" / "wake" / "logic.py"
if not _path.exists():  # the api image (integration runs) has no deploy/; the checkout always does
    pytest.skip("deploy/ is not in the api image", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("wake_logic", _path)
assert _spec and _spec.loader
logic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = logic  # dataclasses look their module up while being created
_spec.loader.exec_module(logic)


@pytest.mark.parametrize("power", ["deallocated", "stopped"])
def test_an_asleep_vm_is_started_and_the_wake_counted(power: str) -> None:
    d = logic.decide_wake(power, False, wakes_today=2, cap=6)
    assert (d.start, d.status) == (True, 202)
    assert d.body == {"power": "starting", "ready": False, "wakes_left": 3}


def test_the_daily_cap_refuses_a_further_start() -> None:
    d = logic.decide_wake("deallocated", False, wakes_today=6, cap=6)
    assert (d.start, d.status, d.body["wakes_left"]) == (False, 429, 0)
    assert "tomorrow" in d.body["message"]


@pytest.mark.parametrize("power", ["running", "starting", "deallocating", "stopping"])
def test_a_vm_that_is_not_asleep_is_never_started_again(power: str) -> None:
    d = logic.decide_wake(power, False, wakes_today=0, cap=6)
    assert not d.start and d.status == 200 and d.body["power"] == power


def test_ready_only_when_running_and_answering() -> None:
    assert logic.decide_status("running", True, 0, 6).body["ready"] is True
    assert logic.decide_status("running", False, 0, 6).body["ready"] is False
    assert logic.decide_status("starting", True, 0, 6).body["ready"] is False  # a stale answer


def test_an_unknown_state_is_reported_not_acted_on() -> None:
    d = logic.decide_wake("unknown", False, 0, 6)
    assert (d.start, d.status) == (False, 503)


def test_the_power_state_is_read_from_the_instance_view() -> None:
    codes = ["ProvisioningState/succeeded", "PowerState/deallocated"]
    assert logic.power_of(codes) == "deallocated"
    assert logic.power_of(["ProvisioningState/updating"]) == "unknown"


def test_the_vm_is_named_by_its_resource_id() -> None:
    rid = "/subscriptions/s1/resourceGroups/rg1/providers/Microsoft.Compute/virtualMachines/vm"
    assert logic.parse_vm_id(rid) == ("s1", "rg1", "vm")
    with pytest.raises(ValueError):
        logic.parse_vm_id("/subscriptions/s1/resourceGroups/rg")
