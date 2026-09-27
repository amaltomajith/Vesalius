"""The proof runner (fake pytest command), the report builder, and the mesh preview."""

from __future__ import annotations

import io
import threading
from pathlib import Path

import numpy as np
import pytest
import trimesh
from PIL import Image

from pipeline.demo import proofs
from pipeline.demo.proofs import PROOFS, ProofRunner, parse_junit
from pipeline.demo.report import build_report_html, render_preview
from pipeline.demo.runner import BusyError

JUNIT_OK = """<testsuites><testsuite tests="3">
<testcase name="a"/><testcase name="b"/><testcase name="c"/></testsuite></testsuites>"""
JUNIT_FAIL = """<testsuites><testsuite><testcase name="a"/>
<testcase name="b"><failure message="boom"/></testcase></testsuite></testsuites>"""
JUNIT_SKIP = """<testsuites><testsuite><testcase name="a"/>
<testcase name="b"><skipped message="needs tesseract"/></testcase></testsuite></testsuites>"""
JUNIT_ERR = """<testsuites><testsuite><testcase name="a"><error message="import"/></testcase>
</testsuite></testsuites>"""


def fake_command(xml: str | None, out: str = "", rc: int = 0):  # type: ignore[no-untyped-def]
    def command(files: list[str], junit_path: str) -> tuple[int, str, str]:
        if xml is not None:
            Path(junit_path).write_text(xml, encoding="utf-8")
        return rc, out, ""

    return command


def run_proof(xml: str | None, out: str = "") -> dict:  # type: ignore[type-arg]
    runner = ProofRunner(command=fake_command(xml, out))
    job = runner.start("face", wait=True)
    result = runner.get(job.id)
    assert result is not None
    return result


def test_junit_is_parsed_into_counts_and_per_test_outcomes() -> None:
    assert parse_junit(JUNIT_OK)[:3] == (3, 0, 0)
    p, f, s, tests = parse_junit(JUNIT_FAIL)
    assert (p, f, s) == (1, 1, 0) and tests[1] == {"name": "b", "outcome": "failed"}
    assert parse_junit(JUNIT_ERR)[:3] == (0, 1, 0)  # a collection/setup error counts as failed
    assert parse_junit(JUNIT_SKIP)[:3] == (1, 0, 1)


@pytest.mark.parametrize(
    "xml, verdict",
    [
        (JUNIT_OK, "passed"),
        (JUNIT_FAIL, "failed"),
        (JUNIT_ERR, "failed"),
        (JUNIT_SKIP, "incomplete"),
        ("<testsuites></testsuites>", "incomplete"),
    ],
)
def test_the_verdict_is_never_passed_unless_everything_ran_and_passed(
    xml: str, verdict: str
) -> None:
    assert run_proof(xml)["verdict"] == verdict


def test_a_skipped_check_is_reported_as_not_fully_run() -> None:
    job = run_proof(JUNIT_SKIP)
    assert "1 skipped (not fully run)" in job["summary"]
    assert job["verdict"] == "incomplete"


def test_a_missing_result_file_is_an_error_not_a_pass() -> None:
    job = run_proof(None)
    assert job["verdict"] == "error" and job["state"] == "finished"
    assert "could not be run" in job["summary"]


def test_the_privacy_count_is_extracted_from_the_proofs_own_output() -> None:
    line = "PHI injection proof: 18 of 18 injected identifiers removed across 2 modalities (['CT'])"
    job = run_proof(JUNIT_OK, out=line)
    assert job["summary"].startswith("18 of 18 planted identifiers removed across 2 kinds of scan")


def test_failure_output_is_kept_only_when_something_did_not_pass() -> None:
    assert run_proof(JUNIT_OK, out="noise")["detail"] == ""
    assert "noise" in run_proof(JUNIT_FAIL, out="noise")["detail"]


def test_only_allow_listed_proofs_can_be_started() -> None:
    runner = ProofRunner(command=fake_command(JUNIT_OK))
    for bad in ("../../etc/passwd", "test_reproducibility.py", "", "PRIVACY", "face; rm -rf /"):
        with pytest.raises(KeyError):
            runner.start(bad)
    assert set(PROOFS) == {"privacy", "face", "rebuild", "contract", "viewer"}


def test_every_allow_listed_proof_file_actually_exists() -> None:
    for proof in PROOFS.values():
        for f in proof.files:
            assert (proofs.REPO_ROOT / f).is_file(), f


def test_the_real_command_never_uses_a_shell_and_takes_no_user_text() -> None:
    import inspect

    src = inspect.getsource(proofs._pytest)
    assert "shell=True" not in src and "os.system" not in src


def test_a_proof_cannot_start_while_another_job_holds_the_lock() -> None:
    lock = threading.Lock()
    runner = ProofRunner(lock=lock, command=fake_command(JUNIT_OK))
    lock.acquire()
    try:
        with pytest.raises(BusyError):
            runner.start("face")
    finally:
        lock.release()
    assert runner.start("face", wait=True) is not None  # released -> runs


# ---- report ---------------------------------------------------------------------


def _run_dict(**over: object) -> dict:  # type: ignore[type-arg]
    base = {
        "id": "abc123",
        "dataset": "head",
        "title": "Head scan",
        "outcome": "success",
        "message": "Done. Your 3D model is ready.",
        "planted": [],
        "steps": [
            {
                "key": "receive",
                "title": "Receive the files",
                "what": "w",
                "state": "done",
                "detail": "Received 106 image files (7.5 MB).",
                "seconds": 3.14,
            }
        ],
    }
    base.update(over)
    return base


def test_the_report_is_self_contained_and_states_it_is_synthetic() -> None:
    html = build_report_html({"run": _run_dict()})
    assert "<!doctype html>" in html and "Head scan" in html and "Received 106 image files" in html
    assert "synthetic" in html and "not evidence about clinical performance" in html
    assert "http://" not in html and "https://" not in html  # no external requests
    assert "3.1s" in html


def test_everything_that_came_from_the_system_is_escaped() -> None:
    evil = "<script>alert(1)</script>"
    run = _run_dict(
        title=evil, message=evil, planted=[{"label": evil, "value": evil, "channel": "t"}]
    )
    run["steps"][0]["detail"] = evil
    html = build_report_html(
        {"run": run, "proofs": [{"title": evil, "verdict": "passed", "summary": evil}]}
    )
    assert "<script>" not in html and html.count("&lt;script&gt;") >= 5


def test_a_rejected_run_produces_a_report_without_result_sections_breaking() -> None:
    html = build_report_html({"run": _run_dict(outcome="rejected", message="Rejected at quality.")})
    assert "Rejected by design" in html and "not available" in html


def test_the_proofs_table_shows_verdicts_or_says_they_were_not_run() -> None:
    assert "have not been run" in build_report_html({"run": _run_dict()})
    html = build_report_html(
        {
            "run": _run_dict(),
            "proofs": [
                {"title": "A", "verdict": "passed", "summary": "ok"},
                {"title": "B", "verdict": "failed", "summary": "no"},
            ],
        }
    )
    assert "pill ok" in html and "pill bad" in html


def _glb(points: np.ndarray) -> bytes:
    mesh = trimesh.Trimesh(vertices=points, faces=[[0, 1, 2]], process=False)
    buf = io.BytesIO()
    mesh.export(file_obj=buf, file_type="glb")
    return buf.getvalue()


def test_the_preview_is_a_real_two_view_image_that_is_not_blank() -> None:
    rng = np.random.default_rng(0)
    pts = rng.normal(size=(500, 3)) * 0.05
    png = render_preview(_glb(pts))
    img = Image.open(io.BytesIO(png))
    assert img.size == (650, 320)
    arr = np.asarray(img)
    assert (arr < 250).sum() > 200  # points were actually drawn, in both halves
    assert (arr[:, :320] < 250).sum() > 100 and (arr[:, 330:] < 250).sum() > 100
