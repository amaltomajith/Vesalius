"""Lineage: derive `lineage_edges` from what every run already records, and query provenance.

Edges depend only on a run's INPUTS, which exist before the run does, so they are written when a
run succeeds (`run_stage` calls `record_edges`) -- one hook covering every stage -- and historical
runs are backfilled with the same function. They are a DERIVED index over immutable recorded
facts (`input_hashes`, `output_hashes`, `artifacts.sha256`), never a second source of truth.

For each input hash `h` of a successful run R:

1. **Artifact-backed input** (S6 onward): artifacts of R's own series with `sha256 = h` ->
   edge(upstream_artifact=A, upstream_run=A.produced_by_run). Series-scoped: identical content in
   two series (a shared fixture, a re-ingested study) gives identical hashes, so an unscoped hash
   lookup silently links across series (found at S11).
2. **Bronze content-hash input** (S1-S5, no artifact carries it): the latest successful run of the
   greatest stage number below R's whose `output_hashes` contain `h`. Content hashes are globally
   unique (`instances.content_hash UNIQUE`), so no scoping is needed.

3. **The defacing gate** (S8, S9, S11 obtain their volume only through `open_defacing_gate`): the
   series' latest successful S7 run that shares an input with R is an upstream dependency even
   when no hash links them -- a "defacing not required" verdict produces no volume artifact, yet
   it is exactly the run that explains why the volume could be used ("absence of defacing must
   always be explained").

An artifact only counts as upstream if a STRICTLY EARLIER stage produced it: S11's gold volume is a
byte-identical copy of the gated volume, so it shares a sha256 with S6's/S7's volume and, at
backfill time, would otherwise appear as an "upstream" of the stages that ran before it existed.

Only `success` runs get edges: a skip or a failure did no work to derive lineage from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import any_, literal, select, text
from sqlalchemy.orm import Session

from pipeline.catalog.models import Artifact, Instance, LineageEdge, TransformRun

# stages that obtain volume bytes only via s7_deface.gate (see that module's docstring)
GATE_CONSUMER_STAGES = frozenset({"S8", "S9", "S11"})
GATE_STAGE = "S7"

MAX_DEPTH = 50  # cycle guard; the real chain is ~12 stages deep


def stage_number(stage: str) -> int:
    """'S7' -> 7. Stage names are S1..S12 (DATA-MODEL.md)."""
    return int(stage[1:])


def _series_of(session: Session, run: TransformRun) -> UUID | None:
    if run.subject_type == "series":
        return run.subject_id
    if run.subject_type == "instance":
        inst = session.get(Instance, run.subject_id)
        return inst.series_id if inst is not None else None
    return None


def _upstream_by_content_hash(session: Session, run: TransformRun, h: str) -> TransformRun | None:
    limit = stage_number(run.stage)
    candidates = (
        session.execute(
            select(TransformRun)
            .where(TransformRun.status == "success", literal(h) == any_(TransformRun.output_hashes))
            .order_by(TransformRun.started_at.desc())
        )
        .scalars()
        .all()
    )
    best: TransformRun | None = None
    for c in candidates:
        n = stage_number(c.stage)
        if n < limit and (best is None or n > stage_number(best.stage)):
            best = c
    return best


def derive_edges(session: Session, run: TransformRun) -> list[tuple[UUID | None, UUID | None]]:
    """(upstream_run, upstream_artifact) pairs for `run`, de-duplicated, order-stable."""
    series_id = _series_of(session, run)
    found: list[tuple[UUID | None, UUID | None]] = []
    for h in run.input_hashes:
        arts: list[Artifact] = []
        if series_id is not None:
            arts = list(
                session.execute(
                    select(Artifact).where(Artifact.series_id == series_id, Artifact.sha256 == h)
                ).scalars()
            )
        earlier: list[Artifact] = []
        for a in arts:
            producer = session.get(TransformRun, a.produced_by_run)
            if producer is not None and stage_number(producer.stage) < stage_number(run.stage):
                earlier.append(a)
        if earlier:
            found += [(a.produced_by_run, a.id) for a in earlier]
            continue
        up = _upstream_by_content_hash(session, run, h)
        if up is not None:
            found.append((up.id, None))
    if run.stage in GATE_CONSUMER_STAGES and series_id is not None:
        gate_run = _gate_run(session, run, series_id)
        if gate_run is not None and gate_run.id not in {r for r, _ in found}:
            found.append((gate_run.id, None))
    return list(dict.fromkeys(found))


def _gate_run(session: Session, run: TransformRun, series_id: UUID) -> TransformRun | None:
    mine = set(run.input_hashes)
    for c in session.execute(
        select(TransformRun)
        .where(
            TransformRun.stage == GATE_STAGE,
            TransformRun.subject_type == "series",
            TransformRun.subject_id == series_id,
            TransformRun.status == "success",
        )
        .order_by(TransformRun.started_at.desc())
    ).scalars():
        if mine & set(c.input_hashes):
            return c
    return None


def record_edges(session: Session, run: TransformRun) -> int:
    """Insert the run's missing edges; returns how many were added. Idempotent."""
    if run.status != "success":
        return 0
    existing = {
        (e.upstream_run, e.upstream_artifact)
        for e in session.execute(
            select(LineageEdge).where(LineageEdge.downstream_run == run.id)
        ).scalars()
    }
    added = 0
    for up_run, up_art in derive_edges(session, run):
        if (up_run, up_art) in existing:
            continue
        session.add(
            LineageEdge(downstream_run=run.id, upstream_run=up_run, upstream_artifact=up_art)
        )
        added += 1
    session.flush()
    return added


def backfill_edges(session: Session) -> int:
    """Edges for every successful run (idempotent)."""
    runs = session.execute(select(TransformRun).where(TransformRun.status == "success")).scalars()
    return sum(record_edges(session, r) for r in list(runs))


# ---- provenance query -------------------------------------------------------------------------

# DATA-MODEL.md's recursive query, with two recorded changes: UNION (not UNION ALL) + DISTINCT ON,
# because S6 has one upstream edge per instance and each fans through S5->S4->S3->S2, so UNION ALL
# re-visits shared runs combinatorially; and a depth cap as a cycle guard.
PROVENANCE_SQL = text(
    """
    WITH RECURSIVE prov AS (
        SELECT tr.id, tr.stage, tr.stage_version, tr.parameters, tr.status, 0 AS depth
        FROM transform_runs tr
        WHERE tr.id = ANY(:roots)
      UNION
        SELECT up.id, up.stage, up.stage_version, up.parameters, up.status, prov.depth + 1
        FROM prov
        JOIN lineage_edges le ON le.downstream_run = prov.id
        JOIN transform_runs up ON up.id = le.upstream_run
        WHERE prov.depth < :max_depth
    )
    SELECT DISTINCT ON (id) id, stage, stage_version, parameters, status, depth
    FROM prov ORDER BY id, depth
    """
)


@dataclass(frozen=True)
class ProvenanceNode:
    run_id: UUID
    stage: str
    stage_version: str
    parameters: dict[str, Any]
    status: str
    depth: int


@dataclass(frozen=True)
class ProvenanceGraph:
    nodes: list[ProvenanceNode]
    edges: list[tuple[UUID, UUID]]  # (downstream_run, upstream_run)


def provenance(session: Session, root_runs: list[UUID]) -> ProvenanceGraph:
    """Full provenance reachable upstream from `root_runs`, nodes ordered by (depth, stage)."""
    if not root_runs:
        return ProvenanceGraph([], [])
    rows = session.execute(PROVENANCE_SQL, {"roots": root_runs, "max_depth": MAX_DEPTH}).all()
    nodes = sorted(
        (
            ProvenanceNode(r.id, r.stage, r.stage_version, dict(r.parameters), r.status, r.depth)
            for r in rows
        ),
        key=lambda n: (n.depth, -stage_number(n.stage), str(n.run_id)),
    )
    ids = [n.run_id for n in nodes]
    edge_rows = session.execute(
        select(LineageEdge.downstream_run, LineageEdge.upstream_run).where(
            LineageEdge.downstream_run.in_(ids),
            LineageEdge.upstream_run.in_(ids),
        )
    ).all()
    edges = sorted(
        {(d, u) for d, u in edge_rows if u is not None}, key=lambda e: (str(e[0]), str(e[1]))
    )
    return ProvenanceGraph(nodes, edges)
