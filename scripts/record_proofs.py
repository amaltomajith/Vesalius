"""Run every proof through the API, one after another, and wait for each verdict.

The static site shows the last recorded proof results (scripts/export_static.py reads
/demo/proofs/last). The API records a proof's result when its finished job is read, so this polls
each job to the end. It reports every verdict honestly and exits non-zero if proof 5 ("viewer":
a head study in the viewer has no face) did not pass, because then nothing may be published.

    python scripts/record_proofs.py --api http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections.abc import Callable
from typing import Any

Call = Callable[[str, str], Any]  # (method, path) -> parsed JSON
REQUIRED = "viewer"


def record(
    call: Call, sleep: Callable[[float], None] = time.sleep, poll: float = 3.0
) -> dict[str, str]:
    """Run each proof in the catalogue; return {key: verdict}."""
    verdicts: dict[str, str] = {}
    for proof in call("GET", "/demo/datasets")["proofs"]:
        key = proof["key"]
        job_id = call("POST", f"/demo/proofs/{key}")["job_id"]
        while True:
            job = call("GET", f"/demo/proofs/jobs/{job_id}")
            if job["state"] == "finished":
                break
            sleep(poll)
        verdicts[key] = str(job.get("verdict"))
        print(f"proof {key}: {verdicts[key]} ({job.get('summary', '')})", flush=True)
    return verdicts


def http_call(api: str) -> Call:
    def call(method: str, path: str) -> Any:
        req = urllib.request.Request(
            api.rstrip("/") + path, method=method, data=b"" if method == "POST" else None
        )
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 -- the local API
            return json.loads(r.read())

    return call


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    verdicts = record(http_call(ap.parse_args().api))
    if verdicts.get(REQUIRED) != "passed":
        print(f"proof {REQUIRED!r} did not pass: nothing may be published", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
