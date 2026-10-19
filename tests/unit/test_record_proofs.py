"""scripts/record_proofs.py: every proof is run and waited for, and its verdict reported as is."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_path = Path(__file__).parents[2] / "scripts" / "record_proofs.py"
_spec = importlib.util.spec_from_file_location("record_proofs", _path)
assert _spec and _spec.loader
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)


class FakeApi:
    def __init__(self, verdicts: dict[str, str], polls_until_done: int = 2) -> None:
        self.verdicts, self.left = verdicts, dict.fromkeys(verdicts, polls_until_done)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str) -> Any:
        self.calls.append((method, path))
        if path == "/demo/datasets":
            return {"proofs": [{"key": k} for k in self.verdicts]}
        if method == "POST":
            return {"job_id": f"job-{path.rsplit('/', 1)[1]}"}
        key = path.rsplit("-", 1)[1]
        self.left[key] -= 1
        if self.left[key] > 0:
            return {"state": "running"}
        return {"state": "finished", "verdict": self.verdicts[key], "summary": "s"}


def test_runs_every_proof_in_turn_and_waits_for_each() -> None:
    api = FakeApi({"privacy": "passed", "viewer": "passed"})
    assert rp.record(api, sleep=lambda s: None) == {"privacy": "passed", "viewer": "passed"}
    posts = [p for m, p in api.calls if m == "POST"]
    assert posts == ["/demo/proofs/privacy", "/demo/proofs/viewer"]
    # the second proof starts only after the first one finished
    first_done = api.calls.index(("GET", "/demo/proofs/jobs/job-privacy"), 3)
    assert first_done < api.calls.index(("POST", "/demo/proofs/viewer"))


def test_reports_a_failed_proof_as_failed() -> None:
    api = FakeApi({"rebuild": "failed", "viewer": "passed"}, polls_until_done=1)
    assert rp.record(api, sleep=lambda s: None)["rebuild"] == "failed"
