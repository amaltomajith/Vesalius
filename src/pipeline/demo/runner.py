"""Runs the pipeline for one scan in a background thread and exposes plain-language progress.

Design rules (each has a test):
  * ONE job at a time -- a second `start` raises `BusyError` instead of running (the heavy
    stages hold large arrays in memory; two at once is how a laptop falls over).
  * Steps run in the pipeline's real order and STOP at the first step that leaves no scan to
    continue with. A rejected scan is a legitimate outcome, not an error: it is explained, and every
    remaining step is marked "skipped".
  * An unexpected exception never escapes the thread and never leaks its message to the page (it
    can carry input-derived text); only its type is shown, the details go to the server log.
  * The stage functions are injected (`Deps`), so ordering and halting are testable with no
    database.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any
from uuid import UUID

from pipeline.common.errors import QuarantineError
from pipeline.demo.explain import STEPS, Step, explain

log = logging.getLogger(__name__)

TARGET_FACES = 100_000  # the demo's 3D model: opens quickly in a browser
MAX_RUNS_KEPT = 20
# the pipeline stage(s) whose quarantine rows explain a stop at each page step
STAGES_OF_STEP: dict[str, tuple[str, ...]] = {
    "catalog": ("S2",),
    "quality": ("S3",),
    "privacy": ("S4", "S5"),
    "volume": ("S6",),
    "face": ("S7",),
    "segment": ("S8",),
    "surface": ("S9",),
    "smooth": ("S10",),
    "package": ("S11",),
}


class BusyError(RuntimeError):
    """A job is already running."""


@dataclass
class StepStatus:
    key: str
    title: str
    what: str
    state: str = "pending"  # pending | running | done | rejected | failed | skipped
    detail: str = ""
    seconds: float | None = None


@dataclass
class RunStatus:
    id: str
    dataset: str
    title: str
    preset: str
    state: str = "running"  # running | finished
    outcome: str | None = None  # success | rejected | error
    message: str = ""
    steps: list[StepStatus] = field(default_factory=list)
    batch_id: str | None = None
    series_ids: list[str] = field(default_factory=list)
    planted: list[dict[str, str]] = field(default_factory=list)
    body_part_choice: str = "auto"
    error_type: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


@dataclass
class Deps:
    publish_contracts: Callable[[], None]
    ingest: Callable[[str, str], Any]  # (zip_path, source) -> IngestBatchResult
    stage: Callable[[str, UUID, str], Any]  # (step key, batch id, preset) -> batch result
    quarantine_reasons: Callable[[UUID, str], list[str]]  # (batch id, step key) -> reason codes
    # (batch id, the person's stated body part) -> plain-language note ("" if nothing was applied)
    declare: Callable[[UUID, str], str] = lambda _batch, _choice: ""


# ---- turning a stage's result into a plain-language outcome ------------------------------------


@dataclass(frozen=True)
class _Outcome:
    ok: int
    bad: int
    detail: str


def _series_bad(series: list[Any]) -> int:
    return sum(
        1 for s in series if getattr(s, "quarantined", False) or getattr(s, "skipped", False)
    )


STRUCTURES = "structures"  # the preset that drives S8-S11 structures mode


def _summarise_structures(step: str, series: list[Any]) -> str:
    if step == "segment":
        n = sum(getattr(s, "structure_count", 0) or 0 for s in series)
        return f"Found {n} named structures (organs, bones and vessels)."
    if step == "surface":
        n = sum(getattr(s, "meshed_count", 0) or 0 for s in series)
        return f"Made a separate surface for each of {n} structures."
    if step == "smooth":
        n = sum(getattr(s, "final_face_count", 0) or 0 for s in series)
        return f"Smoothed them and kept each body system within budget ({n:,} triangles)."
    return "Saved one 3D file per body system and a manifest of every structure."


def _summarise(step: str, r: Any, preset: str) -> _Outcome:
    if step == "receive":
        n = int(r.file_count)
        return _Outcome(n, 0, f"Received {n} image files ({r.bytes_total / 1e6:.1f} MB).")
    if step == "catalog":
        n, bad = len(r.indexed), int(r.quarantined_count)
        extra = f" {bad} could not be read." if bad else ""
        return _Outcome(n, bad, f"Catalogued {n} images.{extra}")
    if step == "quality":
        bad = int(r.quarantined_count)
        ok = len(r.evaluated) - bad
        return _Outcome(ok, bad, "The scan passed." if not bad else "The scan was rejected.")
    if step == "privacy":
        insts = [i for sr in r.series_results for i in sr.results]
        bad = sum(1 for i in insts if getattr(i, "stage_failed", None))
        ok = len(insts) - bad
        return _Outcome(ok, bad, f"{ok} of {ok + bad} images cleaned and independently verified.")
    series = list(r.series_results)
    bad = _series_bad(series)
    ok = len(series) - bad
    if preset == STRUCTURES and step in ("segment", "surface", "smooth", "package"):
        text = _summarise_structures(step, series)
        return _Outcome(ok, bad, text if not bad else "This step did not complete for the scan.")
    if step == "volume":
        text = "Built one evenly spaced 3D volume."
    elif step == "face":
        if getattr(r, "defaced_count", 0):
            text = "Face removed, and an independent check confirmed it."
        else:
            text = (
                "No face removal needed. The record explains why "
                "(the body part is not head-adjacent)."
            )
    elif step == "segment":
        text = f"Selected the {preset.replace('_', ' ')} tissue."
    elif step == "surface":
        n = sum(getattr(s, "face_count", 0) or 0 for s in series)
        text = f"Made a surface of {n:,} triangles." if n else "Made a 3D surface."
    elif step == "smooth":
        n = sum(getattr(s, "final_face_count", 0) or 0 for s in series)
        text = f"Smoothed and reduced to {n:,} triangles." if n else "Smoothed and simplified."
    else:
        text = "Saved the 3D model, a preview picture and the record of how it was made."
    return _Outcome(ok, bad, text if not bad else "This step did not complete for the scan.")


# ---- the runner ----------------------------------------------------------------------------------


class PipelineRunner:
    def __init__(
        self, deps_factory: Callable[[], Deps], lock: threading.Lock | None = None
    ) -> None:
        self._deps_factory = deps_factory
        # held for the whole life of a job; the app passes ONE lock shared with the proof runner,
        # so a proof and a scan can never run at the same time
        self._lock = lock if lock is not None else threading.Lock()
        self._state_lock = threading.Lock()  # guards the run records
        self._runs: dict[str, RunStatus] = {}
        self._order: list[str] = []

    # -- public --
    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def start(
        self,
        *,
        dataset: str,
        title: str,
        preset: str,
        zip_path: str,
        planted: list[dict[str, str]] | None = None,
        cleanup_dir: str | None = None,
        body_part: str = "auto",
        wait: bool = False,
    ) -> RunStatus:
        if not self._lock.acquire(blocking=False):
            raise BusyError("another scan is being processed")
        run = RunStatus(
            id=uuid.uuid4().hex[:12],
            dataset=dataset,
            title=title,
            preset=preset,
            steps=[StepStatus(s.key, s.title, s.what) for s in STEPS],
            planted=planted or [],
            body_part_choice=body_part,
        )
        with self._state_lock:
            self._runs[run.id] = run
            self._order.append(run.id)
            for old in self._order[:-MAX_RUNS_KEPT]:
                self._runs.pop(old, None)
            self._order = self._order[-MAX_RUNS_KEPT:]
        thread = threading.Thread(target=self._work, args=(run, zip_path, cleanup_dir), daemon=True)
        thread.start()
        if wait:
            thread.join()
        return run

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._state_lock:
            run = self._runs.get(run_id)
            return None if run is None else asdict(run)

    def latest_id(self) -> str | None:
        with self._state_lock:
            return self._order[-1] if self._order else None

    # -- internals --
    def _set(self, run: RunStatus, step: StepStatus, **changes: Any) -> None:
        with self._state_lock:
            for k, v in changes.items():
                setattr(step, k, v)

    def _finish(
        self, run: RunStatus, outcome: str, message: str, error_type: str | None = None
    ) -> None:
        with self._state_lock:
            run.state = "finished"
            run.outcome = outcome
            run.message = message
            run.error_type = error_type
            run.finished_at = time.time()

    def _skip_rest(self, run: RunStatus, after: Step, why: str) -> None:
        seen = False
        for s in run.steps:
            if seen and s.state == "pending":
                self._set(run, s, state="skipped", detail=why)
            if s.key == after.key:
                seen = True

    def _work(self, run: RunStatus, zip_path: str, cleanup_dir: str | None = None) -> None:
        current: Step | None = None
        try:
            deps = self._deps_factory()
            deps.publish_contracts()
            batch_id: UUID | None = None
            declared_note = ""
            last: Any = None
            for step in STEPS:
                current = step
                st = next(s for s in run.steps if s.key == step.key)
                self._set(run, st, state="running")
                t0 = time.monotonic()
                try:
                    if step.key == "receive":
                        result = deps.ingest(zip_path, f"demo:{run.dataset}")
                        batch_id = result.batch_id
                        with self._state_lock:
                            run.batch_id = str(batch_id)
                    else:
                        assert batch_id is not None
                        if step.key == "quality" and run.body_part_choice != "auto":
                            declared_note = deps.declare(batch_id, run.body_part_choice)
                        result = deps.stage(step.key, batch_id, run.preset)
                except QuarantineError as exc:
                    self._set(
                        run,
                        st,
                        state="rejected",
                        seconds=time.monotonic() - t0,
                        detail=explain(exc.reason_code),
                    )
                    self._skip_rest(run, step, "Not run, because the scan was rejected.")
                    self._finish(
                        run,
                        "rejected",
                        f"This scan was rejected at the “{step.title}” step. "
                        f"{explain(exc.reason_code)}",
                    )
                    return
                out = _summarise(step.key, result, run.preset)
                if step.key == "quality" and declared_note:
                    out = _Outcome(out.ok, out.bad, f"{declared_note} {out.detail}")
                seconds = time.monotonic() - t0
                last = result
                if out.bad and not out.ok:
                    reasons = deps.quarantine_reasons(batch_id, step.key) if batch_id else []
                    why = " ".join(dict.fromkeys(explain(c) for c in reasons))
                    if not why:
                        why = out.detail  # already carries the stated-body-part note, once
                    elif step.key == "quality" and declared_note:
                        why = f"{declared_note} {why}"
                    self._set(run, st, state="rejected", seconds=seconds, detail=why)
                    self._skip_rest(run, step, "Not run, because the scan was rejected.")
                    self._finish(
                        run, "rejected", f"This scan was rejected at the “{step.title}” step. {why}"
                    )
                    return
                self._set(run, st, state="done", seconds=seconds, detail=out.detail)
            series_ids = [
                str(s.series_id)
                for s in getattr(last, "series_results", [])
                if getattr(s, "manifest_object_key", None)
            ]
            with self._state_lock:
                run.series_ids = series_ids
            self._finish(run, "success", "Done. Your 3D model is ready.")
        except Exception as exc:  # noqa: BLE001 -- the thread must never die silently
            log.exception("demo run %s failed", run.id)
            if current is not None:
                st = next(s for s in run.steps if s.key == current.key)
                self._set(
                    run,
                    st,
                    state="failed",
                    detail="Something unexpected went wrong here. Nothing was lost.",
                )
                self._skip_rest(run, current, "Not run, because an earlier step failed.")
            self._finish(
                run,
                "error",
                "Something unexpected went wrong"
                + (f" at the “{current.title}” step" if current else "")
                + ". The technical details are in the server log.",
                error_type=type(exc).__name__,
            )
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
            self._lock.release()


# ---- real dependencies ---------------------------------------------------------------------


def real_deps() -> Deps:
    """Wires the actual stage functions, in the same argument shapes `pipeline/cli.py` uses."""
    from sqlalchemy import select

    from pipeline.catalog.contracts import publish_all
    from pipeline.catalog.models import IngestBatch, Instance, Quarantine, TransformRun
    from pipeline.catalog.session import get_pipeline_session
    from pipeline.common.config import get_settings
    from pipeline.common.hashing import instance_id_for_content_hash
    from pipeline.demo.declaration import apply_declaration
    from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
    from pipeline.stages.s1_ingest.service import ingest
    from pipeline.stages.s2_parse_index.service import parse_and_index_batch
    from pipeline.stages.s3_quality_gate.service import quality_gate_batch
    from pipeline.stages.s6_volume_assembly.service import assemble_volume_batch
    from pipeline.stages.s7_deface.service import deface_batch
    from pipeline.stages.s8_segment.service import segment_batch
    from pipeline.stages.s8_segment.structures_service import (
        segment_structures_batch,
        series_for_batch,
    )
    from pipeline.stages.s9_surface_extraction.service import extract_surface_batch
    from pipeline.stages.s9_surface_extraction.structures_service import (
        extract_structure_surfaces,
    )
    from pipeline.stages.s10_mesh_postprocess.service import postprocess_mesh_batch
    from pipeline.stages.s10_mesh_postprocess.structures_service import postprocess_structures
    from pipeline.stages.s11_export.service import export_batch
    from pipeline.stages.s11_export.structures_export import export_structures
    from pipeline.storage.object_store import bronze_client, gold_client, silver_client

    settings = get_settings()
    bronze, silver, gold = bronze_client(settings), silver_client(settings), gold_client(settings)

    def publish() -> None:
        with get_pipeline_session(settings) as session:
            publish_all(session)

    def do_ingest(path: str, source: str) -> Any:
        with get_pipeline_session(settings) as session:
            return ingest(session, bronze, settings, source=source, path=path)

    def do_stage(step: str, batch_id: UUID, preset: str) -> Any:
        with get_pipeline_session(settings) as session:
            if step == "catalog":
                return parse_and_index_batch(session, bronze, settings, batch_id)
            if step == "quality":
                return quality_gate_batch(session, bronze, settings, batch_id)
            if step == "privacy":
                return deidentify_and_verify_batch(session, bronze, silver, settings, batch_id)
            if step == "volume":
                return assemble_volume_batch(session, silver, settings, batch_id)
            if step == "face":
                return deface_batch(session, silver, settings, batch_id)
            if preset == STRUCTURES and step in ("segment", "surface", "smooth", "package"):
                return do_structures(session, step, batch_id)
            if step == "segment":
                return segment_batch(session, silver, settings, batch_id, presets=(preset,))
            if step == "surface":
                return extract_surface_batch(session, silver, settings, batch_id, presets=(preset,))
            if step == "smooth":
                return postprocess_mesh_batch(
                    session,
                    silver,
                    settings,
                    batch_id,
                    presets=(preset,),
                    target_faces=TARGET_FACES,
                )
            if step == "package":
                return export_batch(session, silver, gold, settings, batch_id, preset=preset)
        raise KeyError(step)

    def do_structures(session: Any, step: str, batch_id: UUID) -> Any:
        from types import SimpleNamespace

        if step == "segment":
            return segment_structures_batch(session, silver, settings, batch_id)
        sids = series_for_batch(session, batch_id)
        res: list[Any]
        if step == "surface":
            res = [extract_structure_surfaces(session, silver, settings, s) for s in sids]
        elif step == "smooth":
            res = [postprocess_structures(session, silver, settings, s) for s in sids]
        else:
            res = [export_structures(session, silver, gold, settings, s) for s in sids]
        return SimpleNamespace(series_results=res)

    def batch_instance_ids(session: Any, batch_id: UUID) -> list[UUID]:
        # `instances.ingest_batch_id` is never populated, so a batch's instances are found the way
        # every stage finds them: through S1's per-file run records (input hash -> instance id).
        hashes = [
            row[0][0]
            for row in session.execute(
                select(TransformRun.input_hashes).where(
                    TransformRun.stage == "S1",
                    TransformRun.subject_type == "instance",
                    TransformRun.status.in_(("success", "skipped")),
                    TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
                )
            ).all()
            if row[0]
        ]
        return [instance_id_for_content_hash(h) for h in hashes]

    def read_description(instance: Any) -> str | None:
        # header only, and only long enough to classify it; never stored or logged
        import io

        import pydicom

        from pipeline.storage.bronze import bronze_key

        response = bronze.get_object(
            settings.minio_bronze_bucket, bronze_key(instance.content_hash)
        )
        try:
            ds = pydicom.dcmread(io.BytesIO(response.read()), stop_before_pixels=True)
        finally:
            response.close()
            response.release_conn()
        value = getattr(ds, "StudyDescription", None)
        return None if value is None else str(value)

    def declare(batch_id: UUID, choice: str) -> str:
        with get_pipeline_session(settings) as session:
            ids = batch_instance_ids(session, batch_id)
            applied = apply_declaration(session, ids, choice, read_description=read_description)
            return applied.note(choice)

    def reasons(batch_id: UUID, step_key: str) -> list[str]:
        """Why THIS attempt stopped at THIS step: only this step's stage, and only rows written
        since this batch began. The same files may have been refused before (an earlier attempt,
        another stage); those rows are history, not the reason for this stop."""
        with get_pipeline_session(settings) as session:
            began = session.execute(
                select(IngestBatch.started_at).where(IngestBatch.id == batch_id)
            ).scalar_one_or_none()
            instance_ids = batch_instance_ids(session, batch_id)
            series_ids = session.execute(
                select(Instance.series_id).where(Instance.id.in_(instance_ids))
            ).scalars()
            subjects = set(instance_ids) | set(series_ids)
            query = select(Quarantine.reason_code).where(
                Quarantine.subject_id.in_(subjects), Quarantine.stage.in_(STAGES_OF_STEP[step_key])
            )
            ordered = query.order_by(Quarantine.created_at)
            fresh = ordered.where(Quarantine.created_at >= began) if began is not None else ordered
            found = list(dict.fromkeys(session.execute(fresh).scalars()))
            # A step can be answered from an earlier identical evaluation (same files, same inputs):
            # then this stage's rows predate the batch and ARE the reason, so fall back to them.
            return found or list(dict.fromkeys(session.execute(ordered).scalars()))

    return Deps(publish, do_ingest, do_stage, reasons, declare)
