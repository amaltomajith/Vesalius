"""The scan runner's rules, with fake stage functions (no database, no MinIO)."""

from __future__ import annotations

import os
import tempfile
import threading
from types import SimpleNamespace as Ns
from uuid import uuid4

import pytest

from pipeline.common.errors import QuarantineError
from pipeline.demo.explain import STEPS
from pipeline.demo.runner import BusyError, Deps, PipelineRunner

BATCH = uuid4()
SERIES = uuid4()


def _series(**kw: object) -> Ns:
    return Ns(
        quarantined=False,
        skipped=False,
        series_id=SERIES,
        manifest_object_key="k",
        face_count=1234,
        final_face_count=1000,
        **kw,
    )


def happy_result(step: str) -> Ns:
    if step == "catalog":
        return Ns(indexed=[1] * 5, quarantined_count=0)
    if step == "quality":
        return Ns(evaluated=[1], quarantined_count=0)
    if step == "privacy":
        return Ns(series_results=[Ns(results=[Ns(stage_failed=None)] * 5)])
    if step == "face":
        return Ns(series_results=[_series()], defaced_count=1)
    return Ns(series_results=[_series()], defaced_count=0)


class Fake:
    def __init__(
        self,
        *,
        bad_step: str | None = None,
        raise_step: str | None = None,
        quarantine_at_ingest: bool = False,
        reasons: list[str] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.reasons_asked: list[str] = []
        self.bad_step, self.raise_step = bad_step, raise_step
        self.quarantine_at_ingest = quarantine_at_ingest
        self.reasons = reasons or []
        self.release = threading.Event()
        self.wait_at: str | None = None

    def deps(self) -> Deps:
        def ingest(path: str, source: str) -> Ns:
            self.calls.append("receive")
            if self.quarantine_at_ingest:
                raise QuarantineError("S1_ZERO_DICOM_FOUND", {})
            return Ns(batch_id=BATCH, file_count=5, bytes_total=2_000_000)

        def stage(step: str, batch: object, preset: str) -> Ns:
            self.calls.append(step)
            if self.wait_at == step:
                self.release.wait(5)
            if step == self.raise_step:
                raise ValueError("PATIENT^SECRET leaked in an exception message")
            if step == self.bad_step:
                if step == "quality":
                    return Ns(evaluated=[1], quarantined_count=1)
                return Ns(series_results=[Ns(quarantined=True, skipped=False)])
            return happy_result(step)

        return Deps(
            publish_contracts=lambda: self.calls.append("publish"),
            ingest=ingest,
            stage=stage,
            quarantine_reasons=self._reasons,
            declare=self._declare,
        )

    def _reasons(self, batch: object, step_key: str) -> list[str]:
        self.reasons_asked.append(step_key)
        return self.reasons

    def _declare(self, batch: object, choice: str) -> str:
        self.calls.append(f"declare:{choice}")
        return "NOTE-FROM-DECLARE."


def run(fake: Fake, **kw: object) -> dict:  # type: ignore[type-arg]
    runner = PipelineRunner(fake.deps)
    started = runner.start(
        dataset="head", title="Head scan", preset="skin_surface", zip_path="x.zip", wait=True, **kw
    )  # type: ignore[arg-type]
    out = runner.get(started.id)
    assert out is not None
    return out


def test_a_successful_run_walks_all_ten_steps_in_pipeline_order() -> None:
    fake = Fake()
    out = run(fake)
    assert fake.calls == ["publish", "receive"] + [s.key for s in STEPS[1:]]
    assert out["state"] == "finished" and out["outcome"] == "success"
    assert [s["state"] for s in out["steps"]] == ["done"] * 10
    assert out["series_ids"] == [str(SERIES)] and out["batch_id"] == str(BATCH)


def test_contracts_are_published_before_anything_runs() -> None:
    fake = Fake()
    run(fake)
    assert fake.calls[0] == "publish"


def test_step_details_are_plain_language_with_real_numbers() -> None:
    out = run(Fake())
    details = {s["key"]: s["detail"] for s in out["steps"]}
    assert "5 image files" in details["receive"] and "2.0 MB" in details["receive"]
    assert details["catalog"] == "Catalogued 5 images."
    assert "5 of 5 images cleaned" in details["privacy"]
    assert "Face removed" in details["face"]
    assert "1,234 triangles" in details["surface"] and "1,000 triangles" in details["smooth"]


def test_a_rejected_scan_stops_explains_and_skips_the_rest() -> None:
    fake = Fake(bad_step="quality", reasons=["QG_TOO_FEW_SLICES"])
    out = run(fake)
    states = {s["key"]: s["state"] for s in out["steps"]}
    assert states["quality"] == "rejected"
    assert all(states[k] == "skipped" for k in ("privacy", "volume", "face", "package"))
    assert states["receive"] == states["catalog"] == "done"
    assert out["outcome"] == "rejected"
    assert "fewer than 3 images" in out["message"] and "Quality check" in out["message"]
    assert "quality" in fake.calls and "privacy" not in fake.calls  # genuinely stopped


def test_rejection_by_a_quarantine_error_at_ingest_is_explained_too() -> None:
    out = run(Fake(quarantine_at_ingest=True))
    assert out["outcome"] == "rejected" and "did not contain any DICOM" in out["message"]
    assert out["steps"][0]["state"] == "rejected"
    assert all(s["state"] == "skipped" for s in out["steps"][1:])


def test_an_unexpected_error_is_contained_and_its_message_never_reaches_the_page() -> None:
    out = run(Fake(raise_step="volume"))
    assert out["outcome"] == "error" and out["state"] == "finished"
    assert out["error_type"] == "ValueError"
    assert "SECRET" not in repr(out)  # the exception text can carry input-derived data
    states = {s["key"]: s["state"] for s in out["steps"]}
    assert states["volume"] == "failed" and states["face"] == "skipped"


def test_only_one_job_runs_at_a_time() -> None:
    fake = Fake()
    fake.wait_at = "quality"
    runner = PipelineRunner(fake.deps)
    first = runner.start(dataset="head", title="t", preset="bone", zip_path="x.zip")
    for _ in range(200):
        if "quality" in fake.calls:
            break
        threading.Event().wait(0.01)
    assert runner.busy
    with pytest.raises(BusyError):
        runner.start(dataset="chest", title="t", preset="bone", zip_path="y.zip")
    fake.release.set()
    for _ in range(500):
        state = runner.get(first.id)
        if state and state["state"] == "finished":
            break
        threading.Event().wait(0.01)
    assert not runner.busy  # the lock is released even after the job ends


def test_the_lock_is_released_after_an_error_so_the_next_job_can_run() -> None:
    fake = Fake(raise_step="quality")
    runner = PipelineRunner(fake.deps)
    runner.start(dataset="head", title="t", preset="bone", zip_path="x.zip", wait=True)
    assert not runner.busy
    fake.raise_step = None
    runner.start(dataset="head", title="t", preset="bone", zip_path="x.zip", wait=True)


def test_a_lock_can_be_shared_with_another_runner() -> None:
    lock = threading.Lock()
    a, b = PipelineRunner(Fake().deps, lock=lock), PipelineRunner(Fake().deps, lock=lock)
    assert lock.acquire(blocking=False)
    try:
        assert a.busy and b.busy
        with pytest.raises(BusyError):
            b.start(dataset="head", title="t", preset="bone", zip_path="x.zip")
    finally:
        lock.release()


def test_planted_identifiers_are_carried_through_and_the_temp_dir_is_removed() -> None:
    tmp = tempfile.mkdtemp(prefix="demo_test_")
    assert os.path.isdir(tmp)
    planted = [{"label": "Patient name", "value": "DEMO^X", "channel": "tag"}]
    out = run(Fake(), planted=planted, cleanup_dir=tmp)
    assert out["planted"] == planted
    assert not os.path.exists(tmp)


def test_only_the_latest_runs_are_kept() -> None:
    fake = Fake()
    runner = PipelineRunner(fake.deps)
    ids = [
        runner.start(dataset="head", title="t", preset="bone", zip_path="x", wait=True).id
        for _ in range(25)
    ]
    assert runner.get(ids[0]) is None and runner.get(ids[-1]) is not None
    assert runner.latest_id() == ids[-1]


def test_a_stated_body_part_is_applied_after_cataloguing_and_before_the_quality_check() -> None:
    fake = Fake()
    out = run(fake, body_part="chest")
    i = fake.calls.index("declare:chest")
    assert fake.calls[i - 1] == "catalog" and fake.calls[i + 1] == "quality"
    quality = next(s for s in out["steps"] if s["key"] == "quality")
    assert quality["detail"].startswith("NOTE-FROM-DECLARE.")


def test_nothing_is_declared_when_the_files_are_left_to_say() -> None:
    fake = Fake()
    run(fake)  # default "auto"
    assert not any(c.startswith("declare") for c in fake.calls)


def test_the_note_is_shown_on_a_rejection_too() -> None:
    out = run(Fake(bad_step="quality", reasons=["QG_TOO_FEW_SLICES"]), body_part="head")
    assert out["outcome"] == "rejected" and "NOTE-FROM-DECLARE." in out["message"]


def test_the_reasons_asked_for_are_those_of_the_step_that_stopped() -> None:
    fake = Fake(bad_step="quality", reasons=["QG_TOO_FEW_SLICES"])
    run(fake)
    assert fake.reasons_asked == ["quality"]


def test_every_step_maps_to_the_stage_whose_quarantine_rows_explain_it() -> None:
    from pipeline.demo.runner import STAGES_OF_STEP

    assert set(STAGES_OF_STEP) == {s.key for s in STEPS if s.key != "receive"}
    assert all(
        stages and all(s.startswith("S") for s in stages) for stages in STAGES_OF_STEP.values()
    )
