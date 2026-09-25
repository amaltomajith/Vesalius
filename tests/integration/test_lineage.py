"""Lineage against live Postgres: edges are written by `run_stage` itself, the recursive
provenance query walks them, and the graph never leaks across series (identical fixture content
gives identical hashes in different series -- the scoping bug class found at S11).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, func, select

from pipeline.catalog.lineage import backfill_edges, provenance, record_edges, stage_number
from pipeline.catalog.models import Artifact, Instance, LineageEdge, TransformRun
from pipeline.common.config import get_settings
from pipeline.stages.s11_export.service import export_series
from tests.fixtures.shared import integration
from tests.integration.test_s11_export_pipeline import _chest_through_s10, _head_through_s10


def _run_ids(session, series_id, stage: str | None = None) -> list:  # type: ignore[no-untyped-def]
    stmt = select(TransformRun.id).where(
        TransformRun.subject_id == series_id, TransformRun.status == "success"
    )
    if stage:
        stmt = stmt.where(TransformRun.stage == stage)
    return list(session.execute(stmt).scalars())


@integration
def test_a_head_chain_has_exact_shortest_path_depths(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)
    export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    graph = provenance(pipeline_session, _run_ids(pipeline_session, sid, "S11"))

    depth = {n.stage: n.depth for n in graph.nodes}
    # S11 consumes the S10 mesh, the S7-defaced volume and the S6 meta directly; S9 feeds S10;
    # S8's mask feeds S9
    assert depth == {"S11": 0, "S10": 1, "S7": 1, "S6": 1, "S9": 2, "S8": 3}
    stages_by_run = {n.run_id: n.stage for n in graph.nodes}
    edge_stages = {(stages_by_run[d], stages_by_run[u]) for d, u in graph.edges}
    assert {
        ("S11", "S10"),
        ("S11", "S7"),
        ("S11", "S6"),
        ("S10", "S9"),
        ("S9", "S8"),
    } <= edge_stages
    # edges only point to strictly earlier stages
    assert all(stage_number(d) > stage_number(u) for d, u in edge_stages)


@integration
def test_edges_are_written_by_run_stage_and_recording_again_adds_nothing(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)
    export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    runs = list(
        pipeline_session.execute(
            select(TransformRun).where(
                TransformRun.subject_id == sid, TransformRun.status == "success"
            )
        ).scalars()
    )
    edge_count = pipeline_session.execute(
        select(func.count())
        .select_from(LineageEdge)
        .where(LineageEdge.downstream_run.in_([r.id for r in runs]))
    ).scalar_one()
    assert edge_count > 0
    assert sum(record_edges(pipeline_session, r) for r in runs) == 0


@integration
def test_backfill_restores_deleted_edges_exactly(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)
    export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    ids = _run_ids(pipeline_session, sid)

    def snapshot() -> set:  # type: ignore[type-arg]
        return {
            (e.downstream_run, e.upstream_run, e.upstream_artifact)
            for e in pipeline_session.execute(
                select(LineageEdge).where(LineageEdge.downstream_run.in_(ids))
            ).scalars()
        }

    before = snapshot()
    assert before
    pipeline_session.execute(delete(LineageEdge).where(LineageEdge.downstream_run.in_(ids)))
    assert snapshot() == set()
    assert backfill_edges(pipeline_session) >= len(before)
    assert snapshot() == before


@integration
def test_a_real_chest_chain_reaches_ingest_and_never_leaves_its_own_series(
    pipeline_session,
    bronze_minio_client,
    silver_minio_client,
    gold_minio_client,
    no_ocr_engine,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    sid, _ = _chest_through_s10(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "l.zip"
    )
    export_series(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid, "soft_tissue"
    )

    graph = provenance(pipeline_session, _run_ids(pipeline_session, sid, "S11"))

    assert {n.stage for n in graph.nodes} == {f"S{i}" for i in range(1, 12)}
    own_instances = set(
        pipeline_session.execute(select(Instance.id).where(Instance.series_id == sid)).scalars()
    )
    for n in graph.nodes:
        subject = pipeline_session.get(TransformRun, n.run_id).subject_id  # type: ignore[union-attr]
        assert subject == sid or subject in own_instances, (n.stage, subject)
    # the 10 ingested instances each contribute an S1 node
    assert sum(1 for n in graph.nodes if n.stage == "S1") == len(own_instances) == 10
    # the shared-fixture trap: other series exist in the DB with identical content hashes for
    # their own artifacts, yet none of them appear above
    assert pipeline_session.execute(select(func.count()).select_from(Artifact)).scalar_one() > 10


@integration
def test_only_successful_runs_get_edges(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)
    export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    s11 = pipeline_session.execute(
        select(TransformRun).where(TransformRun.subject_id == sid, TransformRun.stage == "S11")
    ).scalar_one()

    def clone(status: str) -> TransformRun:
        run = TransformRun(
            id=uuid4(),
            stage="S11",
            stage_version="1.0.0",
            idempotency_key=uuid4().hex,
            subject_type="series",
            subject_id=sid,
            parameters={},
            input_hashes=list(s11.input_hashes),  # inputs that DO resolve to upstream artifacts
            status=status,
            started_at=datetime.now(UTC),
        )
        pipeline_session.add(run)
        pipeline_session.flush()
        return run

    for status in ("failed", "skipped", "running"):
        assert record_edges(pipeline_session, clone(status)) == 0, status
    assert record_edges(pipeline_session, clone("success")) > 0
