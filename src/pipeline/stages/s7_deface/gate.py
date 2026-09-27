"""The defacing gate: S8, S9 and S11 obtain volume bytes ONLY through `open_defacing_gate`.

Fail closed (CLAUDE.md rule 4): the gate refuses, it never warns. It never decides "this is not
a head" itself -- it only VERIFIES a recorded S7 decision, so an unexplained absence of defacing
is a refusal. It also governs S11's gold volume (a raw head CT NIfTI volume-renders to a face).

Structure: a thin DB/MinIO adapter builds `GateFacts`; a PURE `evaluate_gate(facts)` returns the
verdict (unit-tested exhaustively); the adapter then reads the one permitted object and
re-hashes the bytes it actually got. `GatedVolume` is sealed -- it cannot be constructed
outside this module -- so "the gate passed" and "this is the volume" cannot come apart.

STATED LIMITS: bypass-proof against accidental / structural paths, not against a deliberate
reader of the silver key or a mutable catalog; tamper evidence covers object bytes, not DB edits.
`deid_instance_dicom` silver objects still hold un-defaced pixels and are protected only by
never being served.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID

from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.models import Artifact, Series, TransformRun
from pipeline.common.body_part import VOCAB_VERSION
from pipeline.common.config import Settings
from pipeline.common.errors import DefacingGateError
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s7_deface.decision import decide_deface
from pipeline.stages.s7_deface.service import current_s6_inputs
from pipeline.storage.object_store import get_bytes

_TOKEN = object()

SOURCE_DEFACED = "defaced"
SOURCE_NOT_REQUIRED = "s6_not_required"


@dataclass(frozen=True)
class ArtifactFact:
    object_key: str
    sha256: str


@dataclass(frozen=True)
class RunFact:
    id: UUID
    status: str
    output_hashes: tuple[str, ...]
    deface_block: dict[str, Any]
    vocab_version: str | None


@dataclass(frozen=True)
class GateFacts:
    series_exists: bool
    quality_status: str | None
    s6_volume: ArtifactFact | None
    s6_meta: ArtifactFact | None
    # the LATEST S7 run whose input_hashes equal the current S6 volume+meta hashes, any status
    s7_run: RunFact | None
    s7_any_run_for_series: bool
    required_now: bool | None  # decision recomputed from the CURRENT catalog + recorded headers
    vocab_version_now: str
    defaced: ArtifactFact | None
    report: ArtifactFact | None
    report_body: dict[str, Any] | None


@dataclass(frozen=True)
class GateVerdict:
    source: str  # SOURCE_DEFACED | SOURCE_NOT_REQUIRED
    read: ArtifactFact  # the ONE volume object that may be read
    run_id: UUID
    deface_block: dict[str, Any]


def _refuse(code: str, **detail: object) -> DefacingGateError:
    return DefacingGateError(code, dict(detail))


def evaluate_gate(facts: GateFacts) -> GateVerdict:
    """Pure. Raises DefacingGateError on the first failed check, otherwise names the one
    permitted volume object."""
    if not facts.series_exists:
        raise _refuse("GATE_SERIES_NOT_FOUND")
    if facts.quality_status != "pass":
        raise _refuse("GATE_SERIES_NOT_PASSED", quality_status=facts.quality_status)
    if facts.s6_volume is None or facts.s6_meta is None:
        raise _refuse("GATE_NO_S6_VOLUME")
    run = facts.s7_run
    if run is None:
        if facts.s7_any_run_for_series:
            raise _refuse("GATE_S7_RUN_STALE", detail="no S7 run matches the current S6 volume")
        raise _refuse("GATE_NO_S7_RUN")
    if run.status != "success":
        raise _refuse("GATE_S7_RUN_NOT_SUCCESS", status=run.status, run_id=str(run.id))

    block = run.deface_block
    required = block.get("required")
    reason = block.get("reason")
    if not isinstance(required, bool) or not isinstance(reason, str) or not reason.strip():
        raise _refuse("GATE_DECISION_UNEXPLAINED", run_id=str(run.id))
    if run.vocab_version != facts.vocab_version_now:
        raise _refuse(
            "GATE_VOCAB_STALE", recorded=run.vocab_version, current=facts.vocab_version_now
        )
    if facts.required_now is None:
        raise _refuse("GATE_DECISION_UNVERIFIABLE", run_id=str(run.id))
    if facts.required_now and not required:
        raise _refuse("GATE_DECISION_STALE", detail="defacing is required now but was not applied")

    if facts.report is None or facts.report.sha256 not in run.output_hashes:
        raise _refuse("GATE_REPORT_MISSING", run_id=str(run.id))
    body = facts.report_body or {}
    if body.get("decision", {}).get("reason") != reason or body.get("applied") is not required:
        raise _refuse("GATE_REPORT_DISAGREES", run_id=str(run.id))

    if required:
        if facts.defaced is None:
            raise _refuse("GATE_DEFACED_ARTIFACT_MISSING", run_id=str(run.id))
        if facts.defaced.sha256 not in run.output_hashes:
            raise _refuse("GATE_DEFACED_NOT_IN_RUN_OUTPUTS", run_id=str(run.id))
        recorded = body.get("output", {}).get("volume_defaced") or {}
        if recorded.get("sha256") != facts.defaced.sha256:
            raise _refuse("GATE_REPORT_DISAGREES", run_id=str(run.id))
        return GateVerdict(SOURCE_DEFACED, facts.defaced, run.id, block)

    # not required: the S6 volume itself may be served, because a recorded decision says why
    return GateVerdict(SOURCE_NOT_REQUIRED, facts.s6_volume, run.id, block)


@dataclass(frozen=True)
class GatedVolume:
    """The only volume S8/S9/S11 may use. Sealed: constructing one anywhere but in this module
    raises."""

    series_id: UUID
    volume_bytes: bytes
    volume_sha256: str
    volume_key: str
    meta_bytes: bytes
    meta_sha256: str
    s7_run_id: UUID
    source: str
    _block: dict[str, Any]
    _seal: object = field(repr=False, compare=False, default=None)
    # S7's removed region as recorded in its (hash-verified) report: the RAS thresholds
    # {"a_min", "s_min"} of the face region, or None when the volume was not defaced.
    # Structures mode cuts bone to this region too (user decision, 2026-09-27).
    face_region: dict[str, float] | None = None

    def __post_init__(self) -> None:
        if self._seal is not _TOKEN:
            raise DefacingGateError(
                "GATE_FORGED", {"detail": "GatedVolume is issued by the gate only"}
            )

    def deface_block(self) -> dict[str, Any]:
        """{"required","applied","method","reason"} -- what a downstream manifest records."""
        return {
            "required": self._block["required"],
            "applied": self.source == SOURCE_DEFACED,
            "method": self._block["method"],
            "reason": self._block["reason"],
        }


def _load_facts(session: Session, series_id: UUID) -> GateFacts:
    series = session.get(Series, series_id)
    if series is None:
        return GateFacts(
            False, None, None, None, None, False, None, VOCAB_VERSION, None, None, None
        )

    s6 = current_s6_inputs(session, series_id)
    volume = None if s6 is None else ArtifactFact(s6.volume.object_key, s6.volume.sha256)
    meta = None if s6 is None else ArtifactFact(s6.meta.object_key, s6.meta.sha256)

    s7_runs = (
        session.execute(
            select(TransformRun)
            .where(
                TransformRun.stage == "S7",
                TransformRun.subject_type == "series",
                TransformRun.subject_id == series_id,
            )
            .order_by(TransformRun.started_at.desc())
        )
        .scalars()
        .all()
    )
    matching = None
    if s6 is not None:
        want = sorted([s6.volume.sha256, s6.meta.sha256])
        matching = next((r for r in s7_runs if sorted(r.input_hashes) == want), None)

    run_fact = None
    required_now = None
    defaced = report = None
    report_body: dict[str, Any] | None = None
    if matching is not None:
        p: dict[str, Any] = dict(matching.parameters)
        raw_block = p.get("deface")
        block: dict[str, Any] = dict(raw_block) if isinstance(raw_block, dict) else {}
        raw_vocab = p.get("vocab_version")
        run_fact = RunFact(
            id=matching.id,
            status=matching.status,
            output_hashes=tuple(matching.output_hashes or ()),
            deface_block=block,
            vocab_version=raw_vocab if isinstance(raw_vocab, str) else None,
        )
        raw_evidence = p.get("evidence")
        evidence: dict[str, Any] = dict(raw_evidence) if isinstance(raw_evidence, dict) else {}
        headers = evidence.get("series_header_body_parts")
        study = series.study
        if isinstance(headers, list):
            required_now = decide_deface(
                study_body_part=study.body_part,
                study_body_part_source=study.body_part_source,
                study_deface_required_flag=study.deface_required,
                series_header_body_parts=[str(h) for h in headers],
            ).required
        arts = {
            a.kind: a
            for a in session.execute(
                select(Artifact).where(Artifact.produced_by_run == matching.id)
            )
            .scalars()
            .all()
        }
        if "volume_defaced" in arts:
            defaced = ArtifactFact(arts["volume_defaced"].object_key, arts["volume_defaced"].sha256)
        if "deface_report" in arts:
            report = ArtifactFact(arts["deface_report"].object_key, arts["deface_report"].sha256)
    return GateFacts(
        series_exists=True,
        quality_status=series.quality_status,
        s6_volume=volume,
        s6_meta=meta,
        s7_run=run_fact,
        s7_any_run_for_series=bool(s7_runs),
        required_now=required_now,
        vocab_version_now=VOCAB_VERSION,
        defaced=defaced,
        report=report,
        report_body=report_body,
    )


def _read_verified(client: Minio, settings: Settings, fact: ArtifactFact, code: str) -> bytes:
    data = get_bytes(client, settings.minio_silver_bucket, fact.object_key)
    actual = sha256_of_bytes(data)
    if actual != fact.sha256:
        raise _refuse(
            code, object_key=fact.object_key, catalog_sha256=fact.sha256, actual_sha256=actual
        )
    return data


def _face_region(facts: GateFacts, source: str) -> dict[str, float] | None:
    if source != SOURCE_DEFACED or not facts.report_body:
        return None
    world = facts.report_body.get("region", {}).get("world_mm") or {}
    if "a_min" not in world or "s_min" not in world:
        return None
    return {"a_min": float(world["a_min"]), "s_min": float(world["s_min"])}


def open_defacing_gate(
    session: Session, silver_client: Minio, settings: Settings, series_id: UUID
) -> GatedVolume:
    facts = _load_facts(session, series_id)
    if facts.report is not None:
        # the report body is part of the evidence the pure check compares against the run
        body = json.loads(
            _read_verified(silver_client, settings, facts.report, "GATE_REPORT_HASH_MISMATCH")
        )
        facts = replace(facts, report_body=body)
    verdict = evaluate_gate(facts)
    assert facts.s6_meta is not None
    code = (
        "GATE_DEFACED_HASH_MISMATCH"
        if verdict.source == SOURCE_DEFACED
        else "GATE_S6_HASH_MISMATCH"
    )
    volume_bytes = _read_verified(silver_client, settings, verdict.read, code)
    meta_bytes = _read_verified(silver_client, settings, facts.s6_meta, "GATE_S6_HASH_MISMATCH")
    return GatedVolume(
        series_id=series_id,
        volume_bytes=volume_bytes,
        volume_sha256=verdict.read.sha256,
        volume_key=verdict.read.object_key,
        meta_bytes=meta_bytes,
        meta_sha256=facts.s6_meta.sha256,
        s7_run_id=verdict.run_id,
        source=verdict.source,
        _block=verdict.deface_block,
        _seal=_TOKEN,
        face_region=_face_region(facts, verdict.source),
    )
