"""TESTING.md Proof 3 -- Reproducibility: gold is disposable; lineage is real.

Claim: delete gold and rebuild it from silver and every artifact comes back byte-identical.

    def test_gold_rebuild_is_deterministic():
        original = {a.kind: a.sha256 for a in artifacts_for(series_id)}
        delete_gold(series_id)
        rebuild_from_silver(series_id, pipeline_version=recorded_version)
        rebuilt = {a.kind: a.sha256 for a in artifacts_for(series_id)}
        assert rebuilt == original

Three escalating claims, each measured on the bytes actually STORED in the gold bucket (re-read
and hashed here, never trusted from the catalog -- a catalog row can be honestly wrong):

  (a) same process: wipe gold (objects, catalog rows, the S11 run, its lineage edges -- in FK-safe
      order) and rebuild via `export_series`;
  (b) separate processes with different PYTHONHASHSEEDs: rebuild via the real `pipeline export`
      CLI, twice -- this is what exposes interpreter-level hidden state (hash ordering, cwd,
      environment) that a same-process rebuild cannot;
  (c) two INDEPENDENT series built from identical S6 inputs through S7->S11: the mesh, the
      volume and the thumbnail must match byte for byte; the manifest may differ in `series_id`
      and in nothing else (asserted exactly, so a second differing field cannot hide behind it).

Any mismatch is reported by artifact kind. Per CLAUDE.md rule 8 / TESTING.md rule 4 nothing here
is loosened to pass: a mismatch means hidden state, and the fix belongs in the pipeline.

Integration tier: live Postgres + MinIO (PIPELINE_INTEGRATION_TESTS=1). The head phantom is used
where real mesh work matters (~500k-face decimation); a real S1 chest chain is used where the
CLI needs a batch id. No tesseract needed (no_ocr_engine).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, select

from pipeline.catalog.models import Artifact, LineageEdge, TransformRun
from pipeline.common.config import get_settings
from pipeline.stages.s11_export.service import export_series
from pipeline.storage.object_store import get_bytes
from tests.fixtures.shared import integration
from tests.integration.test_s11_export_pipeline import _chest_through_s10, _head_through_s10

GOLD_KINDS = {"surface_glb", "volume_nifti", "thumbnail", "manifest"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _gold_state(session, gold, settings, series_id: UUID) -> dict[str, dict[str, object]]:  # type: ignore[no-untyped-def]
    """kind -> {catalog_sha, object_sha, bytes} for every gold artifact of the series, where
    object_sha is the sha256 of the bytes actually stored in MinIO right now."""
    rows = session.execute(
        select(Artifact).where(Artifact.series_id == series_id, Artifact.zone == "gold")
    ).scalars()
    state: dict[str, dict[str, object]] = {}
    for a in rows:
        data = get_bytes(gold, settings.minio_gold_bucket, a.object_key)
        state[a.kind] = {"catalog_sha": a.sha256, "object_sha": _sha(data), "bytes": len(data)}
    return state


def _edge_set(session, series_id: UUID) -> set[tuple[UUID | None, UUID | None]]:  # type: ignore[no-untyped-def]
    """(upstream_run, upstream_artifact) of the S11 run's edges. Upstream runs/artifacts are not
    touched by a gold rebuild, so this set must survive it exactly."""
    run_ids = session.execute(
        select(TransformRun.id).where(
            TransformRun.stage == "S11",
            TransformRun.subject_id == series_id,
            TransformRun.status == "success",
        )
    ).scalars()
    return {
        (e.upstream_run, e.upstream_artifact)
        for e in session.execute(
            select(LineageEdge).where(LineageEdge.downstream_run.in_(list(run_ids)))
        ).scalars()
    }


def _wipe_gold(session, gold, settings, series_id: UUID) -> None:  # type: ignore[no-untyped-def]
    """Delete everything gold about the series, in FK-safe order: lineage edges -> gold
    artifact rows -> the S11 run(s) -> the stored objects. Silver, the S1-S10 runs and their
    edges are untouched -- exactly what "delete gold/{series_id}/" means operationally."""
    run_ids = list(
        session.execute(
            select(Artifact.produced_by_run).where(
                Artifact.series_id == series_id, Artifact.zone == "gold"
            )
        ).scalars()
    )
    s11_runs = list(
        session.execute(
            select(TransformRun.id).where(TransformRun.id.in_(run_ids), TransformRun.stage == "S11")
        ).scalars()
    )
    assert s11_runs, "no S11 run to wipe -- nothing was exported"
    originals = {
        a.object_key: a.sha256
        for a in session.execute(
            select(Artifact).where(Artifact.series_id == series_id, Artifact.zone == "gold")
        ).scalars()
    }
    session.execute(delete(LineageEdge).where(LineageEdge.downstream_run.in_(s11_runs)))
    session.execute(
        delete(Artifact).where(Artifact.series_id == series_id, Artifact.zone == "gold")
    )
    session.execute(delete(TransformRun).where(TransformRun.id.in_(s11_runs)))
    session.commit()  # a subprocess rebuild must see the wipe

    _destroy_gold_objects(gold, settings, series_id, originals)


def _destroy_gold_objects(gold, settings, series_id: UUID, originals: dict[str, str]) -> None:  # type: ignore[no-untyped-def]
    """Make the stored gold bytes GONE. The pipeline's gold credential is scoped to
    get/put/list (docker/minio-init/create-buckets.sh) -- no DeleteObject -- so a real delete
    is attempted first and, where denied, each object is overwritten with junk. Either way the
    original bytes must no longer be retrievable before the rebuild starts, or a "rebuild" could
    pass by finding the old objects still in place."""
    from minio.error import S3Error

    from pipeline.storage.object_store import put_bytes

    for key in originals:
        try:
            gold.remove_object(settings.minio_gold_bucket, key)
        except S3Error as exc:
            if exc.code != "AccessDenied":
                raise
            put_bytes(gold, settings.minio_gold_bucket, key, b"GOLD DESTROYED", "text/plain")
    for key, original_sha in originals.items():
        try:
            now = _sha(get_bytes(gold, settings.minio_gold_bucket, key))
        except S3Error:
            continue  # really deleted
        assert now != original_sha, f"{key}: the original bytes survived the wipe"


def _diff(original: dict, rebuilt: dict) -> list[str]:  # type: ignore[type-arg]
    """Every mismatch, by artifact kind -- none suppressed."""
    out: list[str] = []
    for kind in sorted(set(original) | set(rebuilt)):
        o, r = original.get(kind), rebuilt.get(kind)
        if o is None or r is None:
            out.append(f"{kind}: present only in {'rebuilt' if o is None else 'original'}")
            continue
        for field in ("object_sha", "catalog_sha", "bytes"):
            if o[field] != r[field]:
                out.append(
                    f"{kind}.{field}: original={str(o[field])[:16]} rebuilt={str(r[field])[:16]}"
                )
    return out


def _assert_catalog_is_honest(state: dict) -> None:  # type: ignore[type-arg]
    for kind, s in state.items():
        assert s["catalog_sha"] == s["object_sha"], (
            f"{kind}: the catalog sha256 does not match the bytes stored in gold"
        )


# ---- (a) same-process rebuild ------------------------------------------------------------------


@integration
def test_gold_rebuild_is_deterministic(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _head_through_s10(pipeline_session, silver_minio_client, settings)
    assert not export_series(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid
    ).skipped

    original = _gold_state(pipeline_session, gold_minio_client, settings, sid)
    original_edges = _edge_set(pipeline_session, sid)
    assert set(original) == GOLD_KINDS and original_edges
    _assert_catalog_is_honest(original)

    _wipe_gold(pipeline_session, gold_minio_client, settings, sid)
    assert _gold_state(pipeline_session, gold_minio_client, settings, sid) == {}

    rebuilt_result = export_series(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid
    )
    assert not rebuilt_result.skipped and not rebuilt_result.quarantined, rebuilt_result
    rebuilt = _gold_state(pipeline_session, gold_minio_client, settings, sid)

    mismatches = _diff(original, rebuilt)
    assert not mismatches, "Proof 3(a) FAILED -- hidden state:\n  " + "\n  ".join(mismatches)
    _assert_catalog_is_honest(rebuilt)
    assert _edge_set(pipeline_session, sid) == original_edges, "lineage changed across a rebuild"


# ---- (b) separate-process rebuilds under different hash seeds ------------------------------------


def _cli_export(batch_id: UUID, preset: str, hashseed: str) -> None:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    r = subprocess.run(
        [sys.executable, "-m", "pipeline.cli", "export", str(batch_id), "--preset", preset],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert r.returncode == 0, r.stderr
    result = json.loads(r.stdout)["series_results"][0]
    assert not result["skipped"] and not result["quarantined"], result


@integration
def test_rebuilds_in_separate_processes_with_different_hash_seeds_are_identical(
    pipeline_session,
    bronze_minio_client,
    silver_minio_client,
    gold_minio_client,
    no_ocr_engine,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    sid, batch_id = _chest_through_s10(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "p3.zip"
    )
    export_series(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid, "soft_tissue"
    )
    pipeline_session.commit()  # the CLI subprocess reads the same catalog
    original = _gold_state(pipeline_session, gold_minio_client, settings, sid)
    assert set(original) == GOLD_KINDS

    for seed in ("1", "12345"):
        _wipe_gold(pipeline_session, gold_minio_client, settings, sid)
        _cli_export(batch_id, "soft_tissue", seed)
        pipeline_session.expire_all()
        rebuilt = _gold_state(pipeline_session, gold_minio_client, settings, sid)
        mismatches = _diff(original, rebuilt)
        assert not mismatches, (
            f"Proof 3(b) FAILED with PYTHONHASHSEED={seed} -- interpreter-level hidden state:\n  "
            + "\n  ".join(mismatches)
        )


# ---- (c) two independent series from identical inputs --------------------------------------------


@integration
def test_two_independent_series_from_identical_inputs_produce_identical_gold(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    states = []
    for _ in range(2):
        sid = _head_through_s10(pipeline_session, silver_minio_client, settings)
        assert not export_series(
            pipeline_session, silver_minio_client, gold_minio_client, settings, sid
        ).skipped
        states.append((sid, _gold_state(pipeline_session, gold_minio_client, settings, sid)))
    (sid_a, a), (sid_b, b) = states
    assert sid_a != sid_b and set(a) == set(b) == GOLD_KINDS

    # the mesh, the volume and the thumbnail depend only on the shared inputs
    for kind in ("surface_glb", "volume_nifti", "thumbnail"):
        assert a[kind]["object_sha"] == b[kind]["object_sha"], (
            f"Proof 3(c) FAILED -- {kind} differs between two series built from identical inputs"
        )

    # the manifest may differ in `series_id` and in NOTHING else
    def manifest(sid: UUID) -> dict:  # type: ignore[type-arg]
        art = pipeline_session.execute(
            select(Artifact).where(
                Artifact.series_id == sid, Artifact.zone == "gold", Artifact.kind == "manifest"
            )
        ).scalar_one()
        return json.loads(get_bytes(gold_minio_client, settings.minio_gold_bucket, art.object_key))

    ma, mb = manifest(sid_a), manifest(sid_b)
    assert ma.pop("series_id") == str(sid_a) and mb.pop("series_id") == str(sid_b)
    assert ma == mb, "the manifests differ in more than series_id"


@pytest.mark.parametrize("name", ["_wipe_gold", "_diff", "_gold_state", "_edge_set"])
def test_the_proof_helpers_exist(name: str) -> None:
    """Guard against the proof silently degrading to a no-op if a helper is renamed away."""
    assert callable(globals()[name])


def test_the_diff_helper_can_actually_report_a_mismatch() -> None:
    same = {"k": {"object_sha": "a", "catalog_sha": "a", "bytes": 1}}
    assert _diff(same, same) == []
    other = {"k": {"object_sha": "b", "catalog_sha": "a", "bytes": 1}}
    assert _diff(same, other) == ["k.object_sha: original=a rebuilt=b"]
    assert _diff(same, {}) == ["k: present only in original"]
