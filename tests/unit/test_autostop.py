"""scripts/deploy/autostop.sh: the on-demand live backend goes back to sleep, and only then.

It deallocates the Azure VM (a stopped VM is still billed) through the VM's managed identity. A
wrong decision either burns the student credit (never stopping) or cuts a visitor's scan short.
Run with fake `docker` and `curl` on PATH: the decision, and whether the deallocate call is made.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "deploy" / "autostop.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not shutil.which("bash"), reason="needs a Linux shell (CI, the api image)"
)

FAKE_DOCKER = """#!/bin/sh
[ -n "$FAKE_IDLE_JSON" ] || exit 1   # the api is not up yet
echo "$FAKE_IDLE_JSON"
"""
FAKE_CURL = """#!/bin/sh
echo "$@" >> "$CALLS"
case "$*" in
  *oauth2/token*) echo '{"access_token": "tok"}' ;;
  *compute/resourceId*) echo "$VM_ID" ;;
esac
"""
VM_ID = "/subscriptions/s/resourceGroups/g/providers/Microsoft.Compute/virtualMachines/v"


def _run(tmp_path: Path, uptime: int, idle_json: str | None) -> tuple[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("docker", FAKE_DOCKER), ("curl", FAKE_CURL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "AUTOSTOP_UPTIME": str(uptime),
        "FAKE_IDLE_JSON": idle_json or "",
        "CALLS": str(calls),
        "VM_ID": VM_ID,
    }
    out = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip(), calls.read_text()


def _idle(seconds: int, busy: bool = False) -> str:
    return f'{{"idle_seconds": {seconds}.0, "busy": {"true" if busy else "false"}}}'


@pytest.mark.parametrize(
    ("uptime", "idle_json", "stops"),
    [
        (300, _idle(60), False),  # someone is using it
        (1200, _idle(900), True),  # 15 idle minutes
        (1200, _idle(899), False),
        (3600, _idle(5), True),  # the session limit
        (3600, _idle(900, busy=True), False),  # never mid-scan before the hard limit
        (10800, _idle(0, busy=True), True),  # the hard limit, even mid-job
        (300, None, False),  # api still starting: idle since boot, not yet long
        (1200, None, True),  # api never came up: no reason to keep paying
    ],
)
def test_it_deallocates_exactly_when_it_should(
    tmp_path: Path, uptime: int, idle_json: str | None, stops: bool
) -> None:
    decision, calls = _run(tmp_path, uptime, idle_json)
    assert decision.startswith("autostop: stop" if stops else "autostop: keep"), decision
    deallocated = [c for c in calls.splitlines() if c.rstrip().endswith("api-version=2024-07-01")]
    if stops:
        assert len(deallocated) == 1 and "-X POST" in deallocated[0]
        assert "/virtualMachines/v/deallocate" in deallocated[0]
        assert "Authorization: Bearer tok" in deallocated[0]
    else:
        assert calls == "", "kept, yet it asked Azure for something"


def test_the_limits_are_configurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VESALIUS_IDLE_MINUTES", "30")
    decision, _ = _run(tmp_path, 1200, _idle(900))
    assert decision.startswith("autostop: keep")
