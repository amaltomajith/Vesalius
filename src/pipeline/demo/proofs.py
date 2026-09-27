"""Runs the four project proofs (tests/proofs/) from the interface and turns the result into
pass/fail cards.

Safety: the set of runnable proofs is a FIXED allow-list of file paths. Nothing a user sends ever
reaches the command line -- the request carries only a proof key that is looked up here.

Each proof is executed by `pytest` in a subprocess (a fresh interpreter: no leaked state, and a
crash or timeout cannot take the web server down). The junit XML it writes is parsed into counts,
so a card says exactly how many checks passed, failed or were skipped. A proof with any skipped or
missing check is reported as INCOMPLETE, never as passed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET  # noqa: S405 -- parses pytest's own junit file, not user input
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pipeline.demo.runner import BusyError

REPO_ROOT = Path(__file__).resolve().parents[3]
PROOF_IMAGE = REPO_ROOT / "tests" / "proofs" / "_out" / "defacing_side_by_side.png"
TIMEOUT_SECONDS = 1200


@dataclass(frozen=True)
class Proof:
    key: str
    number: int
    title: str
    claim: str
    plain: str  # what it means, for a non-technical reader
    files: tuple[str, ...]
    approx_seconds: int


PROOFS: dict[str, Proof] = {
    "privacy": Proof(
        "privacy",
        1,
        "Patient identifiers are removed",
        "De-identifying the header is not enough: identifiers also hide in private vendor data and in text burned into the pixels.",
        "Fake names, IDs, dates and a phone number are planted in a scan, including hidden and picture-embedded ones. The check counts how many survive.",
        ("tests/proofs/test_phi_injection.py",),
        30,
    ),
    "face": Proof(
        "face",
        2,
        "A face can never be emitted",
        "The pipeline cannot produce an identifying face mesh, and the safety gate cannot be bypassed.",
        "A head that skipped face removal is refused; swapping the original volume in for the face-removed one is caught; and the same head is shown with and without face removal.",
        ("tests/proofs/test_defacing_gate.py", "tests/proofs/test_defacing_gate_system.py"),
        90,
    ),
    "rebuild": Proof(
        "rebuild",
        3,
        "Results can be rebuilt exactly",
        "Delivered results are disposable: delete them, rebuild from the stored source data, and every file comes back byte for byte identical.",
        "The published files are destroyed and rebuilt three different ways (same program, a fresh program, two independent copies). All must match exactly.",
        ("tests/proofs/test_reproducibility.py",),
        240,
    ),
    "contract": Proof(
        "contract",
        4,
        "Format changes are caught",
        "Consumers are protected from upstream schema drift, above all from a silent left-right mirror.",
        "A new data format that quietly flips left and right is published. A downstream user of the data refuses it instead of producing mirrored results.",
        (
            "tests/proofs/test_contract_violation.py",
            "tests/proofs/test_contract_violation_published.py",
        ),
        90,
    ),
    "viewer": Proof(
        "viewer",
        5,
        "The 3D viewer never shows a face",
        "A head study reaches the 3D viewer only defaced: every named structure, bone included, is cut to the face region, and the viewer itself refuses a study that is not.",
        "A head goes through face removal and structure mode, then is loaded exactly the way the viewer loads it. No part of any served 3D model lies where the face was. The same check on the uncut study does find the face, so the check is real.",
        ("tests/proofs/test_head_study_has_no_face.py",),
        60,
    ),
}


@dataclass
class ProofJob:
    id: str
    key: str
    state: str = "running"  # running | finished
    verdict: str | None = None  # passed | failed | incomplete | error
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    tests: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    detail: str = ""  # tail of the output, kept only when something did not pass
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


Command = Callable[[list[str], str], tuple[int, str, str]]  # (files, junit_path) -> (rc, out, err)


def _pytest(files: list[str], junit_path: str) -> tuple[int, str, str]:
    env = {**os.environ, "PIPELINE_INTEGRATION_TESTS": "1", "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(  # noqa: S603 -- fixed allow-listed files, list argv, no shell
        [
            sys.executable,
            "-m",
            "pytest",
            *files,
            "-q",
            "-s",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit_path}",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=TIMEOUT_SECONDS,
    )
    return proc.returncode, proc.stdout, proc.stderr


def parse_junit(xml_text: str) -> tuple[int, int, int, list[dict[str, str]]]:
    """(passed, failed, skipped, tests) from a junit XML document."""
    root = ET.fromstring(xml_text)  # noqa: S314
    tests: list[dict[str, str]] = []
    passed = failed = skipped = 0
    for case in root.iter("testcase"):
        name = case.get("name", "?")
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
            failed += 1
        elif case.find("skipped") is not None:
            outcome = "skipped"
            skipped += 1
        else:
            outcome = "passed"
            passed += 1
        tests.append({"name": name, "outcome": outcome})
    return passed, failed, skipped, tests


_PHI_LINE = re.compile(
    r"PHI injection proof: (\d+) of (\d+) injected identifiers removed across (\d+)"
)


def _verdict(passed: int, failed: int, skipped: int) -> str:
    if failed:
        return "failed"
    if skipped or passed == 0:
        return "incomplete"
    return "passed"


class ProofRunner:
    def __init__(self, lock: threading.Lock | None = None, command: Command = _pytest) -> None:
        self._lock = lock if lock is not None else threading.Lock()
        self._command = command
        self._jobs: dict[str, ProofJob] = {}
        self._state_lock = threading.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def start(self, key: str, *, wait: bool = False) -> ProofJob:
        if key not in PROOFS:
            raise KeyError(key)
        if not self._lock.acquire(blocking=False):
            raise BusyError("another job is running")
        job = ProofJob(id=uuid.uuid4().hex[:12], key=key)
        with self._state_lock:
            self._jobs[job.id] = job
        thread = threading.Thread(target=self._work, args=(job,), daemon=True)
        thread.start()
        if wait:
            thread.join()
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._state_lock:
            job = self._jobs.get(job_id)
            return None if job is None else asdict(job)

    def _work(self, job: ProofJob) -> None:
        proof = PROOFS[job.key]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                junit = str(Path(tmp) / "junit.xml")
                rc, out, err = self._command(list(proof.files), junit)
                text = Path(junit).read_text(encoding="utf-8") if Path(junit).exists() else ""
            if not text:
                raise RuntimeError(f"no result file written (exit code {rc})")
            passed, failed, skipped, tests = parse_junit(text)
            verdict = _verdict(passed, failed, skipped)
            summary = f"{passed} checks passed"
            if failed:
                summary += f", {failed} FAILED"
            if skipped:
                summary += f", {skipped} skipped (not fully run)"
            m = _PHI_LINE.search(out)
            if m:
                summary = (
                    f"{m.group(1)} of {m.group(2)} planted identifiers removed "
                    f"across {m.group(3)} kinds of scan. {summary}."
                )
            detail = "" if verdict == "passed" else "\n".join((out + "\n" + err).splitlines()[-60:])
            with self._state_lock:
                job.passed, job.failed, job.skipped, job.tests = passed, failed, skipped, tests
                job.verdict, job.summary, job.detail = verdict, summary, detail
        except Exception as exc:  # noqa: BLE001 -- never let the thread die silently
            with self._state_lock:
                job.verdict = "error"
                job.summary = f"The proof could not be run ({type(exc).__name__})."
        finally:
            with self._state_lock:
                job.state = "finished"
                job.finished_at = time.time()
            self._lock.release()


def catalogue() -> list[dict[str, object]]:
    return [
        {
            "key": p.key,
            "number": p.number,
            "title": p.title,
            "claim": p.claim,
            "plain": p.plain,
            "approx_seconds": p.approx_seconds,
        }
        for p in PROOFS.values()
    ]
