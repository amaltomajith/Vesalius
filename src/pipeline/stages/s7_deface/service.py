"""S7 -- Deface. See refs/STAGES.md S7, refs/GEOMETRY.md, and the S7 plan.

Reads the CURRENT S6 volume (latest successful S6 run's artifacts -- S6 overwrites
`{series}/volume.nii.gz` in place, so "the artifact at this key" is not a safe handle) and
records a decision for EVERY series that reaches it:

* required     -> a defaced copy is written to a run-scoped key (silver stays immutable), checked
                  by an independent verifier on the persisted bytes, and reported;
* not required -> nothing is written but the report, whose decision carries a non-empty reason
                  (absence of defacing is always explained).

Both outcomes are `status='success'` transform_runs; the gate (gate.py) is the only reader that
decides what may be meshed or exported. Skips are reserved for readiness gaps (no S6 volume yet).
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import pydicom
from minio import Minio
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import (
    StageContext,
    StageOutcome,
    idempotency_key,
    record_skip,
    run_stage,
)
from pipeline.catalog.models import Artifact, Instance, Quarantine, Series, TransformRun
from pipeline.common.config import Settings
from pipeline.common.errors import (
    DefaceAffineMismatchError,
    DefaceEfficacyCheckFailedError,
    DefaceInputTamperedError,
    DefaceModalityUnsupportedError,
    DefaceOutputHeaderMismatchError,
    DefaceUnreadableInputError,
    QuarantineError,
)
from pipeline.common.hashing import instance_id_for_content_hash, sha256_of_bytes
from pipeline.stages.s1_ingest.service import STAGE as S1_STAGE
from pipeline.stages.s6_volume_assembly.affine import parse_nifti_sform
from pipeline.stages.s7_deface import nifti_io, params
from pipeline.stages.s7_deface.algorithm import deface_region
from pipeline.stages.s7_deface.decision import DefaceDecision, decide_deface
from pipeline.stages.s7_deface.geometry import WorldFrame, voxel_bbox, world_bounds
from pipeline.stages.s7_deface.models import BatchDefaceResult, SeriesDefaceResult
from pipeline.stages.s7_deface.report import build_report, packed_mask_sha256
from pipeline.stages.s7_deface.verify import verify_defacing
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION

UNREADABLE_HEADER = "UNREADABLE"  # sentinel: classifies as unrecognised => required


@dataclass(frozen=True)
class S6Inputs:
    run: TransformRun
    volume: Artifact
    meta: Artifact


def current_s6_inputs(session: Session, series_id: UUID) -> S6Inputs | None:
    """The latest SUCCESSFUL S6 run for the series and its two artifacts."""
    run = session.execute(
        select(TransformRun)
        .where(
            TransformRun.stage == "S6",
            TransformRun.subject_type == "series",
            TransformRun.subject_id == series_id,
            TransformRun.status == "success",
        )
        .order_by(TransformRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None:
        return None
    arts = {
        a.kind: a
        for a in session.execute(
            select(Artifact).where(
                Artifact.produced_by_run == run.id,
                Artifact.kind.in_(("volume_nifti", "volume_meta")),
            )
        )
        .scalars()
        .all()
    }
    if "volume_nifti" not in arts or "volume_meta" not in arts:
        return None
    return S6Inputs(run=run, volume=arts["volume_nifti"], meta=arts["volume_meta"])


def _silver_instance_key(series_id: UUID, pseudo_sop_uid: str) -> str:
    return f"{series_id}/instances/{pseudo_sop_uid}.dcm"


def gather_header_body_parts(
    silver_client: Minio, settings: Settings, series_id: UUID, meta_artifact: Artifact
) -> list[str | None]:
    """BodyPartExamined from the first and last silver instance (slice order). Never raises:
    anything unreadable becomes UNREADABLE, which the decision treats as "required"."""
    try:
        meta = json.loads(
            get_bytes(silver_client, settings.minio_silver_bucket, meta_artifact.object_key)
        )
        uids = list(meta["quality"]["slice_order_pseudo_sop_uids"])
    except Exception:  # noqa: BLE001 -- evidence gathering must not fail the decision
        return [UNREADABLE_HEADER]
    picks = [uids[0], uids[-1]] if uids else []
    found: list[str | None] = []
    for uid in dict.fromkeys(picks):
        try:
            data = get_bytes(
                silver_client, settings.minio_silver_bucket, _silver_instance_key(series_id, uid)
            )
            ds = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)
            value = getattr(ds, "BodyPartExamined", None)
            found.append(None if value is None else str(value))
        except Exception:  # noqa: BLE001
            found.append(UNREADABLE_HEADER)
    return found


def _read(client: Minio, settings: Settings, artifact: Artifact) -> bytes:
    try:
        data = get_bytes(client, settings.minio_silver_bucket, artifact.object_key)
    except Exception as exc:  # noqa: BLE001 -- infra failure, S6's precedent: quarantine
        raise DefaceUnreadableInputError(
            {"object_key": artifact.object_key, "error": f"{type(exc).__name__}: {exc}"}
        ) from exc
    actual = sha256_of_bytes(data)
    if actual != artifact.sha256:
        raise DefaceInputTamperedError(
            {
                "object_key": artifact.object_key,
                "catalog_sha256": artifact.sha256,
                "actual_sha256": actual,
            }
        )
    return data


def _ref(artifact: Artifact) -> dict[str, str]:
    return {"object_key": artifact.object_key, "sha256": artifact.sha256}


@dataclass
class _Produced:
    outcome: StageOutcome
    volume_bytes: bytes | None
    volume_key: str | None
    report_bytes: bytes
    report_key: str


def _execute(
    *,
    silver_client: Minio,
    settings: Settings,
    series: Series,
    s6: S6Inputs,
    decision: DefaceDecision,
    prefix: str,
) -> _Produced:
    meta_bytes = _read(silver_client, settings, s6.meta)
    try:
        meta = json.loads(meta_bytes)
    except ValueError as exc:
        raise DefaceUnreadableInputError(
            {"object_key": s6.meta.object_key, "error": str(exc)}
        ) from exc

    report_key = f"{prefix}/deface_report.json"
    volume_ref, meta_ref = _ref(s6.volume), _ref(s6.meta)

    if not decision.required:
        report = build_report(
            series_id=series.id,
            decision=decision,
            volume_ref=volume_ref,
            meta_ref=meta_ref,
            defaced_ref=None,
            report_key=report_key,
            detail=None,
        )
        return _Produced(
            StageOutcome(output_hashes=[sha256_of_bytes(report)]), None, None, report, report_key
        )

    intensity = meta.get("intensity", {})
    if meta.get("modality") != "CT" or not intensity.get("hu_semantics_valid"):
        raise DefaceModalityUnsupportedError(
            {
                "modality": meta.get("modality"),
                "hu_semantics_valid": intensity.get("hu_semantics_valid"),
            }
        )

    volume_bytes = _read(silver_client, settings, s6.volume)
    try:
        loaded = nifti_io.load_volume(volume_bytes)
    except Exception as exc:  # noqa: BLE001
        raise DefaceUnreadableInputError(
            {"object_key": s6.volume.object_key, "error": f"{type(exc).__name__}: {exc}"}
        ) from exc

    meta_affine = np.array(meta["spatial"]["affine"], dtype=float)
    if list(loaded.array.shape) != list(meta["array"]["shape"]) or not np.allclose(
        meta_affine, loaded.sform, atol=params.ROUND_TRIP_TOLERANCE
    ):
        raise DefaceAffineMismatchError(
            {
                "meta_shape": meta["array"]["shape"],
                "volume_shape": list(loaded.array.shape),
                "meta_affine": meta_affine.tolist(),
                "sform": loaded.sform.tolist(),
            }
        )

    air_value = float(intensity["air_value"])
    spacing = float(meta["spatial"]["spacing_mm"][0])
    result = deface_region(loaded.array, loaded.sform, air_value=air_value, spacing_mm=spacing)

    out_bytes = nifti_io.write_like(loaded.image, result.defaced)
    out_header = nifti_io.header_bytes(out_bytes)
    cmp = nifti_io.compare_headers(loaded.header, out_header, params.ROUND_TRIP_TOLERANCE)
    out_sform = parse_nifti_sform(out_header)
    sform_equal = bool(np.allclose(out_sform, loaded.sform, atol=params.ROUND_TRIP_TOLERANCE))
    if not (cmp.equivalent and sform_equal):
        raise DefaceOutputHeaderMismatchError(
            {
                "header_bytes_exact": cmp.exact,
                "non_geometry_identical": cmp.non_geometry_identical,
                "geometry_close": cmp.geometry_close,
                "sform_equal": sform_equal,
                "source_sform": loaded.sform.tolist(),
                "output_sform": out_sform.tolist(),
            }
        )

    # Verify the PERSISTED bytes, not the in-memory array: covers the write path too.
    reread = nifti_io.load_volume(out_bytes)
    if not np.array_equal(reread.array, result.defaced):
        raise DefaceEfficacyCheckFailedError({"failures": ["write_path_altered_voxels"]})
    verdict = verify_defacing(
        loaded.array, reread.array, reread.sform, air_value=air_value, spacing_mm=spacing
    )
    if not verdict.passed:
        raise DefaceEfficacyCheckFailedError(
            {"failures": verdict.failures, "metrics": verdict.metrics}
        )

    frame = WorldFrame.from_affine(loaded.sform)
    outside_region_changed = int((result.changed_mask & ~result.region_mask).sum())
    detail: dict[str, Any] = {
        "sanity": {
            k: result.stats[k]
            for k in (
                "body_volume_ml",
                "ap_extent_mm",
                "s_extent_mm",
                "head_width_mm",
                "bone_voxels",
            )
        },
        "head_extent_mm": result.stats["head_extent_mm"],
        "region": {
            "world_mm": result.stats["region_world_mm"],
            "voxels": result.stats["region_voxels"],
            "voxel_bbox_zyx": voxel_bbox(result.region_mask),
            "ras_bounds_mm": world_bounds(frame, result.region_mask),
        },
        "change": {
            "changed_voxels": result.stats["changed_voxels"],
            "changed_fraction_of_body": result.stats["changed_fraction_of_body"],
            "bbox_zyx": voxel_bbox(result.changed_mask),
            "packed_mask_sha256": packed_mask_sha256(result.changed_mask),
            "bone_voxels_changed": verdict.metrics["bone_voxels_changed"],
            "outside_region_changed": outside_region_changed,
            "removal_value": air_value,
        },
        "efficacy": {
            "passed": verdict.passed,
            "metrics": verdict.metrics,
            "verifier_version": params.VERIFIER_VERSION,
        },
        "header_check": {
            "header_bytes_exact": cmp.exact,
            "non_geometry_identical": cmp.non_geometry_identical,
            "geometry_close": cmp.geometry_close,
            "sform_equal": sform_equal,
        },
    }
    volume_key = f"{prefix}/volume_defaced.nii.gz"
    defaced_ref = {"object_key": volume_key, "sha256": sha256_of_bytes(out_bytes)}
    report = build_report(
        series_id=series.id,
        decision=decision,
        volume_ref=volume_ref,
        meta_ref=meta_ref,
        defaced_ref=defaced_ref,
        report_key=report_key,
        detail=detail,
    )
    return _Produced(
        StageOutcome(output_hashes=[defaced_ref["sha256"], sha256_of_bytes(report)]),
        out_bytes,
        volume_key,
        report,
        report_key,
    )


def deface_series(
    session: Session, silver_client: Minio, settings: Settings, series_id: UUID
) -> SeriesDefaceResult:
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    def _skip(reason: str) -> SeriesDefaceResult:
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={"series_id": str(series_id), "skip": reason},
            ),
            reason=reason,
        )
        return SeriesDefaceResult(series_id=series_id, skipped=True, skip_reason=reason)

    if series.quality_status != "pass":
        return _skip("series_not_passed_quality_gate")
    s6 = current_s6_inputs(session, series_id)
    if s6 is None:
        return _skip("series_has_no_s6_volume")

    study = series.study
    decision = decide_deface(
        study_body_part=study.body_part,
        study_body_part_source=study.body_part_source,
        study_deface_required_flag=study.deface_required,
        series_header_body_parts=gather_header_body_parts(
            silver_client, settings, series_id, s6.meta
        ),
    )
    run_params: dict[str, Any] = {
        "series_id": str(series_id),
        "params_version": params.PARAMS_VERSION,
        "vocab_version": decision.vocab_version,
        "deface": decision.to_block(),
        "evidence": decision.evidence,
    }
    if decision.required:
        run_params["algorithm"] = params.algorithm_params()
    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted([s6.volume.sha256, s6.meta.sha256]),
        params=run_params,
    )
    prefix = f"{series_id}/defaced/{idempotency_key(ctx)[:16]}"
    holder: list[_Produced] = []

    def _do() -> StageOutcome:
        produced = _execute(
            silver_client=silver_client,
            settings=settings,
            series=series,
            s6=s6,
            decision=decision,
            prefix=prefix,
        )
        if produced.volume_bytes is not None and produced.volume_key is not None:
            put_bytes(
                silver_client,
                settings.minio_silver_bucket,
                produced.volume_key,
                produced.volume_bytes,
                "application/gzip",
            )
        put_bytes(
            silver_client,
            settings.minio_silver_bucket,
            produced.report_key,
            produced.report_bytes,
            "application/json",
        )
        holder.append(produced)
        return produced.outcome

    try:
        run, executed = run_stage(session, ctx, _do)
    except QuarantineError as exc:
        session.add(
            Quarantine(
                id=uuid4(),
                subject_type="series",
                subject_id=series_id,
                stage=STAGE,
                reason_code=exc.reason_code,
                detail=exc.detail,
            )
        )
        session.flush()
        return SeriesDefaceResult(
            series_id=series_id,
            quarantined=True,
            reason_code=exc.reason_code,
            required=decision.required,
            reason=decision.reason,
        )

    if not executed:
        # reconstruct from the catalog, never from closure state
        block = run.parameters["deface"]
        assert isinstance(block, dict)
        arts = {
            a.kind: a
            for a in session.execute(select(Artifact).where(Artifact.produced_by_run == run.id))
            .scalars()
            .all()
        }
        if "deface_report" not in arts or (block["required"] and "volume_defaced" not in arts):
            raise AssertionError(
                f"S7 transform_run {run.id} succeeded previously but artifact rows are missing "
                "-- idempotency invariant violated"
            )
        defaced = arts.get("volume_defaced")
        return SeriesDefaceResult(
            series_id=series_id,
            required=bool(block["required"]),
            applied=defaced is not None,
            method=str(block["method"]),
            reason=str(block["reason"]),
            defaced_object_key=None if defaced is None else defaced.object_key,
            report_object_key=arts["deface_report"].object_key,
        )

    produced = holder[0]
    if produced.volume_bytes is not None and produced.volume_key is not None:
        session.add(
            Artifact(
                id=uuid4(),
                series_id=series_id,
                kind="volume_defaced",
                format="nifti-1-gzip",
                zone="silver",
                object_key=produced.volume_key,
                sha256=sha256_of_bytes(produced.volume_bytes),
                bytes=len(produced.volume_bytes),
                contract_version="1.0",
                produced_by_run=run.id,
            )
        )
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series_id,
            kind="deface_report",
            format="json",
            zone="silver",
            object_key=produced.report_key,
            sha256=sha256_of_bytes(produced.report_bytes),
            bytes=len(produced.report_bytes),
            produced_by_run=run.id,
        )
    )
    session.flush()
    return SeriesDefaceResult(
        series_id=series_id,
        required=decision.required,
        applied=produced.volume_bytes is not None,
        method=decision.method,
        reason=decision.reason,
        defaced_object_key=produced.volume_key,
        report_object_key=produced.report_key,
    )


def _content_hashes_for_batch(session: Session, batch_id: UUID) -> list[str]:
    """Mirrors the identical helper in S2-S6 (consolidation deferred rather than touching
    verified code)."""
    stmt = select(TransformRun.input_hashes).where(
        TransformRun.stage == S1_STAGE,
        TransformRun.subject_type == "instance",
        TransformRun.status.in_(("success", "skipped")),
        TransformRun.parameters["ingest_batch_id"].astext == str(batch_id),
    )
    return [row[0][0] for row in session.execute(stmt).all() if row[0]]


def deface_batch(
    session: Session, silver_client: Minio, settings: Settings, batch_id: UUID
) -> BatchDefaceResult:
    instance_ids = [
        instance_id_for_content_hash(h) for h in _content_hashes_for_batch(session, batch_id)
    ]
    series_ids = (
        session.execute(select(Instance.series_id).where(Instance.id.in_(instance_ids)).distinct())
        .scalars()
        .all()
    )
    results = [deface_series(session, silver_client, settings, sid) for sid in series_ids]
    return BatchDefaceResult(
        batch_id=batch_id,
        series_results=results,
        defaced_count=sum(1 for r in results if r.applied),
        not_required_count=sum(1 for r in results if r.required is False and not r.skipped),
    )
