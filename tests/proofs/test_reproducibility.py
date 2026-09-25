"""TESTING.md Proof 3 -- Reproducibility: gold is disposable; lineage is real.

Claim: deleting gold and rebuilding from silver produces bit-identical artifacts.

    def test_gold_rebuild_is_deterministic():
        original = {a.kind: a.sha256 for a in artifacts_for(series_id)}

        delete_gold(series_id)
        rebuild_from_silver(series_id, pipeline_version=recorded_version)

        rebuilt = {a.kind: a.sha256 for a in artifacts_for(series_id)}
        assert rebuilt == original

TESTING.md determinism hazards (all must be addressed before this passes):
- Timestamps in file headers -> S11 GLB exporter pins to a fixed EPOCH_MS constant
- Dict iteration order in JSON -> sort_keys=True throughout (S11 manifest builder)
- Non-deterministic mesh library thread scheduling -> S10 postprocess single-threaded
- Any unseeded randomness -> none; geometry chain is deterministic given the same inputs

If the hashes do not match, the pipeline has hidden state.  Per TESTING.md rule 4 the test
does NOT suppress or paper over the mismatch -- it reports which artifact kind mismatched
so the search is narrowed to one stage.

Implementation note on forcing the re-run:
`run_stage` returns (run, False) when an existing successful transform_run with the same
idempotency key is found.  To make S11 redo its work, we delete the existing S11
transform_run rows for the series so `run_stage` sees no prior success and re-executes.
The silver inputs are untouched; only the gold objects and the S11 run rows are cleared.
This is exactly what "delete gold/{series_id}/ and rebuild" means in operational terms.

Integration tier: requires live Postgres + MinIO (PIPELINE_INTEGRATION_TESTS=1).
CHEST CT only -- no defacing required, so tesseract is not needed (no_ocr_engine fixture).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import delete, select

from pipeline.catalog.idempotency import StageContext, idempotency_key
from pipeline.catalog.models import Artifact, TransformRun
from pipeline.common.config import get_settings
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s6_volume_assembly.service import assemble_volume_series
from pipeline.stages.s7_deface.service import deface_series
from pipeline.stages.s8_segment.params import DEFAULT_PRESET as S8_DEFAULT_PRESET
from pipeline.stages.s8_segment.service import segment_series
from pipeline.stages.s9_surface_extraction.params import DEFAULT_PRESET as S9_DEFAULT_PRESET
from pipeline.stages.s9_surface_extraction.service import extract_surface
from pipeline.stages.s10_mesh_postprocess.service import postprocess_mesh_series
from pipeline.stages.s11_export.params import DEFAULT_PRESET as S11_DEFAULT_PRESET
from pipeline.stages.s11_export.service import export_series
from pipeline.storage.object_store import get_bytes, put_bytes
from tests.fixtures.archive_builder import build_zip
from tests.fixtures.dicom_factory import build_clean_ct_series
from tests.fixtures.shared import integration


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _gold_artifacts(session, series_id: UUID) -> dict[str, str]:
    """kind -> sha256 for every gold-zone artifact of the series."""
    rows = (
        session.execute(
            select(Artifact).where(
                Artifact.series_id == series_id,
                Artifact.zone == "gold",
            )
        )
        .scalars()
        .all()
    )
    return {a.kind: a.sha256 for a in rows}


def _delete_gold_objects(gold_client, settings, series_id: UUID) -> None:
    """Remove all gold objects for the series from the object store.

    MinIO `list_objects` with a prefix iterates the actual objects; we remove each one so
    the re-run is forced to regenerate (rather than reading a cached copy and re-hashing).
    """
    from minio.error import S3Error

    prefix = str(series_id)
    try:
        objects = list(
            gold_client.list_objects(settings.minio_gold_bucket, prefix=prefix, recursive=True)
        )
        for obj in objects:
            gold_client.remove_object(settings.minio_gold_bucket, obj.object_name)
    except S3Error:
        pass  # bucket or objects may not exist yet; re-run will write fresh ones


def _delete_s11_transform_runs(session, series_id: UUID) -> None:
    """Delete S11 transform_run rows for the series so run_stage re-executes.

    We cannot simply change the idempotency key (that would be a hidden-state fix, not a
    rebuild).  Deleting the rows is the correct minimal perturbation: the silver inputs are
    untouched, only the audit record of the first export is cleared.  On the next call
    to `export_series`, `run_stage` finds no prior success and re-runs _do().
    """
    session.execute(
        delete(TransformRun).where(
            TransformRun.stage == "S11",
            TransformRun.subject_type == "series",
            TransformRun.subject_id == series_id,
        )
    )
    session.flush()


def _run_full_pipeline_chest(
    session, bronze, silver, gold, settings, tmp_path: Path, tag: str
) -> UUID:
    """Ingest a 5-slice CHEST CT, run S1 through S11, return the series_id.

    CHEST: no defacing required -> no tesseract dependency -> bare-host compatible.
    """
    blobs = build_clean_ct_series(count=5, body_part="CHEST")
    archive = build_zip(tmp_path, f"{tag}.zip", {f"scan{i}.dcm": b for i, b in enumerate(blobs)})

    batch = ingest(session, bronze, settings, "proof3", str(archive))
    parse_and_index_batch(session, bronze, settings, batch.batch_id)
    quality_gate_batch(session, bronze, settings, batch.batch_id)

    deid = deidentify_and_verify_batch(session, bronze, silver, settings, batch.batch_id)
    failed = [r for sr in deid.series_results for r in sr.results if r.stage_failed]
    assert not failed, f"S4/S5 failures: {failed}"

    series_id: UUID = deid.series_results[0].series_id

    r6 = assemble_volume_series(session, silver, settings, series_id)
    assert not r6.skipped and not r6.quarantined, f"S6 failed: {r6}"

    r7 = deface_series(session, silver, settings, series_id)
    assert not r7.quarantined, f"S7 failed: {r7}"

    r8 = segment_series(session, silver, settings, series_id, preset=S8_DEFAULT_PRESET)
    assert not r8.skipped and not r8.quarantined, f"S8 failed: {r8}"

    r9 = extract_surface(session, silver, settings, series_id, preset=S9_DEFAULT_PRESET)
    assert not r9.quarantined, f"S9 failed: {r9}"
    assert not r9.skipped, (
        f"S9 skipped ({r9.skip_reason}): for CHEST with a complete S7 run the gate should pass"
    )

    r10 = postprocess_mesh_series(session, silver, settings, series_id, preset=S9_DEFAULT_PRESET)
    assert not r10.skipped and not r10.quarantined, f"S10 failed: {r10}"

    r11 = export_series(session, silver, gold, settings, series_id, preset=S11_DEFAULT_PRESET)
    assert not r11.skipped and not r11.quarantined, f"S11 failed: {r11}"
    assert r11.manifest_object_key, "S11 produced no manifest"

    return series_id


@integration
def test_gold_rebuild_is_deterministic(
    pipeline_session,
    bronze_minio_client,
    silver_minio_client,
    gold_minio_client,
    no_ocr_engine,
    tmp_path: Path,
) -> None:
    """TESTING.md Proof 3: delete gold, re-run S11 from silver, hashes must be identical.

    If this test fails, the pipeline has hidden state.  The error message reports which
    artifact kind mismatched; that narrows the investigation to one stage.  Common culprits:

    - GLB: a timestamp embedded in the glTF binary by trimesh/pygltflib.
      Fix: S11 must pass mtime=0 / a fixed epoch to the GLB writer.
    - manifest: a wall-clock "produced_at" field not seeded from the run.
      Fix: use the transform_run`s `started_at` as the manifest timestamp.
    - thumbnail: PIL`s PNG encoder embeds a creation-time tEXt chunk.
      Fix: save with `optimize=False` and strip the tIME chunk.
    - volume: SimpleITK embeds creation time in the NIfTI header`s `glmax` field or
      `aux_file` extension.  Fix: zero those fields before writing.

    Per TESTING.md rule 4: do not weaken this assertion.  A documented failure is more
    valuable than a suppressed one.
    """
    settings = get_settings()

    series_id = _run_full_pipeline_chest(
        pipeline_session,
        bronze_minio_client,
        silver_minio_client,
        gold_minio_client,
        settings,
        tmp_path,
        tag="proof3",
    )

    original_hashes = _gold_artifacts(pipeline_session, series_id)
    assert original_hashes, "no gold artifacts found after first S11 run"
    assert {"surface_glb", "volume_nifti", "manifest"}.issubset(original_hashes), (
        f"expected surface_glb, volume_nifti, manifest in gold; got {set(original_hashes)}"
    )

    # --- simulate "delete gold" ---
    _delete_gold_objects(gold_minio_client, settings, series_id)
    _delete_s11_transform_runs(pipeline_session, series_id)

    # --- rebuild from silver (same silver inputs; S11 re-runs _do() afresh) ---
    r11_rebuilt = export_series(
        pipeline_session,
        silver_minio_client,
        gold_minio_client,
        settings,
        series_id,
        preset=S11_DEFAULT_PRESET,
    )
    assert not r11_rebuilt.skipped and not r11_rebuilt.quarantined, (
        f"S11 rebuild was skipped or quarantined: {r11_rebuilt}"
    )
    assert r11_rebuilt.manifest_object_key, "S11 rebuild produced no manifest"

    rebuilt_hashes = _gold_artifacts(pipeline_session, series_id)

    # --- compare -- every mismatch is reported, none suppressed ---
    mismatches: list[str] = []
    for kind, original_sha in original_hashes.items():
        rebuilt_sha = rebuilt_hashes.get(kind)
        if rebuilt_sha != original_sha:
            mismatches.append(
                f"{kind}: original={original_sha[:16]}... "
                f"rebuilt={str(rebuilt_sha)[:16]}..."
            )
    for kind in rebuilt_hashes:
        if kind not in original_hashes:
            mismatches.append(f"{kind}: appeared only in rebuilt run")

    assert not mismatches, (
        "Proof 3 FAILED -- the pipeline has hidden state.\n"
        "Mismatches (artifact kind: original sha vs rebuilt sha):\n"
        + "\n".join(f"  {m}" for m in mismatches)
        + "\n\nSee the docstring above for investigation hints."
    )
