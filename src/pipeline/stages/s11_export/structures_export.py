"""S11 `structures` mode -- per-group multi-node GLBs + `structures.json` into gold
(refs/INTEGRATION.md §2 S11 and §3 "The mesh contract").

Governance, as in the threshold export: the defacing gate is opened first (a refusal is a
`skipped` run), inputs are S10's structures output read by catalog row and re-hashed, and every
gold object is re-read after writing and compared. Additionally, the node names of every GLB are
read back with an independent GLB reader and must equal the structures listed for that group --
"The entire integration depends on this" (refs/KICKOFF.md, B2).

Gold layout (refs §2):
    gold/{series_id}/mesh/{group}.glb        kind mesh_group_glb     one node per structure
    gold/{series_id}/mesh/structures.json    kind structures_manifest (mesh_contract 1.0)
    gold/{series_id}/thumbnail.png           kind thumbnail           (shared with threshold mode)

Gold rows are upserted per OBJECT KEY (the threshold export's per-kind upsert assumes one row per
kind, which several group GLBs would break).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import numpy as np
from minio import Minio
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.idempotency import (
    StageContext,
    StageOutcome,
    idempotency_key,
    record_skip,
    run_stage,
)
from pipeline.catalog.models import Artifact, Quarantine, Series, Study
from pipeline.catalog.structures_lookup import (
    artifacts_of_run,
    latest_structures_artifacts,
    read_verified,
)
from pipeline.common.config import Settings
from pipeline.common.errors import DefacingGateError, ExportOutputMismatchError, QuarantineError
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s7_deface.gate import open_defacing_gate
from pipeline.stages.s9_surface_extraction import structure_mesh_io as sm_io
from pipeline.stages.s11_export import group_glb, params, thumbnail
from pipeline.storage.object_store import get_bytes, put_bytes

STAGE = params.STAGE
STAGE_VERSION = params.STAGE_VERSION
MESH_CONTRACT_NAME = "mesh_contract"
MESH_CONTRACT_VERSION = "1.0"
EXPORT_FRAME = "glTF_Y_up_m"
GROUP_ORDER = (
    "skeletal",
    "cardiovascular",
    "visceral",
    "respiratory",
    "muscular",
    "nervous",
    "endocrine",
    "other",
)
GROUP_LABELS = {g: g.capitalize() for g in GROUP_ORDER}


class SeriesStructuresExportResult(BaseModel):
    series_id: UUID
    skipped: bool = False
    skip_reason: str | None = None
    gate_reason_code: str | None = None
    quarantined: bool = False
    reason_code: str | None = None
    manifest_object_key: str | None = None
    group_object_keys: list[str] = []
    total_bytes: int | None = None


def structures_algorithm_params(pipeline_version: str) -> dict[str, object]:
    return {
        "mode": "structures",
        "contract_name": MESH_CONTRACT_NAME,
        "contract_version": MESH_CONTRACT_VERSION,
        "export_frame": EXPORT_FRAME,
        "pipeline_version": pipeline_version,
    }


def build_structures_manifest(
    *,
    series_id: UUID,
    pipeline_version: str,
    segmentation: dict[str, Any],
    offset_mm: np.ndarray,
    deface: dict[str, Any],
    groups: list[dict[str, Any]],
    not_meshed: list[dict[str, str]],
    study: dict[str, Any],
) -> bytes:
    doc = {
        "contract_version": MESH_CONTRACT_VERSION,
        "series_id": str(series_id),
        "pipeline_version": pipeline_version,
        "segmentation": {
            "model": segmentation["engine"]["engine"],
            "model_version": segmentation["engine"]["engine_version"],
            "weights_sha256": segmentation["engine"]["weights_sha256"],
            "label_set_version": segmentation["label_set_version"],
            "group_map_version": segmentation["group_map_version"],
        },
        "geometry": {
            "export_frame": EXPORT_FRAME,
            "units": "metres",
            "shared_origin": True,
            "centring_offset_mm": [round(float(x), 4) for x in offset_mm],
            "bbox_frame": "RAS_mm",
        },
        "deface": {
            "required": deface["required"],
            "applied": deface["applied"],
            "method": deface.get("method"),
            # head studies: every structure (bone included) cut to S7's face region
            "structures_cut": deface.get("structures_cut"),
        },
        "groups": groups,
        "not_meshed": not_meshed,
        "study": study,
    }
    return json.dumps(doc, sort_keys=True, indent=1).encode()


def _upsert_gold_by_key(
    session: Session,
    *,
    series_id: UUID,
    kind: str,
    format_: str,
    object_key: str,
    data: bytes,
    run_id: UUID,
    contract_version: str | None,
) -> None:
    existing = session.execute(
        select(Artifact).where(
            Artifact.series_id == series_id,
            Artifact.zone == "gold",
            Artifact.object_key == object_key,
        )
    ).scalar_one_or_none()
    sha = sha256_of_bytes(data)
    if existing is not None:
        existing.kind, existing.format, existing.sha256 = kind, format_, sha
        existing.bytes, existing.contract_version, existing.produced_by_run = (
            len(data),
            contract_version,
            run_id,
        )
        return
    session.add(
        Artifact(
            id=uuid4(),
            series_id=series_id,
            kind=kind,
            format=format_,
            zone="gold",
            object_key=object_key,
            sha256=sha,
            bytes=len(data),
            contract_version=contract_version,
            produced_by_run=run_id,
        )
    )


def export_structures(
    session: Session, silver_client: Minio, gold_client: Minio, settings: Settings, series_id: UUID
) -> SeriesStructuresExportResult:
    series = session.get(Series, series_id)
    if series is None:
        raise ValueError(f"no series with id {series_id}")

    def _skip(reason: str, gate_code: str | None = None) -> SeriesStructuresExportResult:
        full = reason if gate_code is None else f"{reason}:{gate_code}"
        record_skip(
            session,
            StageContext(
                stage=STAGE,
                stage_version=STAGE_VERSION,
                subject_type="series",
                subject_id=series_id,
                input_hashes=[],
                params={
                    "series_id": str(series_id),
                    "mode": "structures",
                    "skip": reason,
                    **({"gate_code": gate_code} if gate_code else {}),
                },
            ),
            reason=full,
        )
        return SeriesStructuresExportResult(
            series_id=series_id, skipped=True, skip_reason=full, gate_reason_code=gate_code
        )

    try:
        gated = open_defacing_gate(session, silver_client, settings, series_id)
    except DefacingGateError as exc:
        return _skip("defacing_gate_refused", exc.reason_code)
    found = latest_structures_artifacts(
        session, "S10", series_id, ("structure_meshes_optimized", "structure_postprocess_report")
    )
    if found is None:
        return _skip("series_has_no_s10_structures")
    _, arts = found

    ctx = StageContext(
        stage=STAGE,
        stage_version=STAGE_VERSION,
        subject_type="series",
        subject_id=series_id,
        input_hashes=sorted(
            [
                arts["structure_meshes_optimized"].sha256,
                arts["structure_postprocess_report"].sha256,
                gated.volume_sha256,
                gated.meta_sha256,
            ]
        ),
        params={
            "series_id": str(series_id),
            "params_version": params.PARAMS_VERSION,
            "algorithm": structures_algorithm_params(settings.pipeline_version),
            "deface": gated.deface_block(),
        },
    )
    _ = idempotency_key(ctx)
    prefix = str(series_id)
    holder: list[dict[str, bytes]] = []

    def _do() -> StageOutcome:
        report: dict[str, Any] = json.loads(
            read_verified(
                silver_client, settings, arts["structure_postprocess_report"], stage=STAGE
            )
        )
        packed = read_verified(
            silver_client, settings, arts["structure_meshes_optimized"], stage=STAGE
        )
        meshes = {m.node: m for m in sm_io.unpack(packed)}
        normals = sm_io.unpack_normals(packed)
        offset = group_glb.shared_offset_mm([m.vertices_ras for m in meshes.values()])
        by_group: dict[str, list[dict[str, Any]]] = {}
        for s in report["structures"]:
            by_group.setdefault(str(s["group"]), []).append(s)

        outputs: dict[str, bytes] = {}
        groups_doc: list[dict[str, Any]] = []
        for group in [g for g in GROUP_ORDER if g in by_group] + sorted(
            set(by_group) - set(GROUP_ORDER)
        ):
            items = sorted(by_group[group], key=lambda s: str(s["node"]))
            parts = []
            for s in items:
                m = meshes[str(s["node"])]
                v, n = group_glb.to_gltf(m.vertices_ras, normals[m.node], offset)
                parts.append((m.node, v, m.faces, n))
            glb = group_glb.build_group_glb(parts)
            expected = sorted(str(s["node"]) for s in items)
            if group_glb.glb_node_names(glb) != expected:
                raise ExportOutputMismatchError(
                    {
                        "group": group,
                        "reason": "node_names_did_not_survive_export",
                        "expected": expected,
                        "found": group_glb.glb_node_names(glb),
                    }
                )
            key = f"{prefix}/mesh/{group}.glb"
            outputs[key] = glb
            groups_doc.append(
                {
                    "id": group,
                    "label": GROUP_LABELS.get(group, group.capitalize()),
                    "file": f"mesh/{group}.glb",
                    "bytes": len(glb),
                    "structures": [
                        {
                            "node": s["node"],
                            "label": s["label"],
                            "faces": int(s["faces"]),
                            "volume_ml": s["volume_ml"],
                            "hu_mean": s["hu_mean"],
                            "hu_std": s["hu_std"],
                            "bbox_mm": s["bbox_mm"],
                        }
                        for s in items
                    ],
                }
            )
        study = session.get(Study, series.study_id)
        manifest = build_structures_manifest(
            series_id=series_id,
            pipeline_version=settings.pipeline_version,
            segmentation=report["segmentation"],
            offset_mm=offset,
            deface={
                **gated.deface_block(),
                "structures_cut": (
                    report.get("face_cut", {}).get("method")
                    if report.get("face_cut", {}).get("applied")
                    else None
                ),
            },
            groups=groups_doc,
            not_meshed=report["not_meshed"],
            study={
                "modality": study.modality if study else None,
                "body_part": study.body_part if study else None,
                "slice_count": series.instance_count,
                "voxel_spacing_mm": report["voxel_spacing_mm"],
                "shifted_study_date": (
                    study.shifted_study_date.isoformat()
                    if study and study.shifted_study_date
                    else None
                ),
            },
        )
        outputs[f"{prefix}/mesh/structures.json"] = manifest
        outputs[f"{prefix}/thumbnail.png"] = thumbnail.render_thumbnail(
            gated.volume_bytes
        ).png_bytes
        for key, data in outputs.items():
            ctype = (
                "model/gltf-binary"
                if key.endswith(".glb")
                else ("application/json" if key.endswith(".json") else "image/png")
            )
            put_bytes(gold_client, settings.minio_gold_bucket, key, data, ctype)
            if sha256_of_bytes(
                get_bytes(gold_client, settings.minio_gold_bucket, key)
            ) != sha256_of_bytes(data):
                raise ExportOutputMismatchError(
                    {"object_key": key, "reason": "gold_reread_differs"}
                )
        holder.append(outputs)
        return StageOutcome(output_hashes=sorted(sha256_of_bytes(d) for d in outputs.values()))

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
        return SeriesStructuresExportResult(
            series_id=series_id, quarantined=True, reason_code=exc.reason_code
        )

    if not executed:
        gold = {a.object_key: a for a in artifacts_of_run(session, run.id).values()}
        rows = (
            session.execute(
                select(Artifact).where(
                    Artifact.series_id == series_id,
                    Artifact.zone == "gold",
                    Artifact.produced_by_run == run.id,
                )
            )
            .scalars()
            .all()
        )
        keys = sorted(a.object_key for a in rows) or sorted(gold)
        return SeriesStructuresExportResult(
            series_id=series_id,
            manifest_object_key=f"{prefix}/mesh/structures.json",
            group_object_keys=[k for k in keys if k.endswith(".glb")],
            total_bytes=sum(a.bytes for a in rows),
        )

    outputs = holder[0]
    for key, data in outputs.items():
        if key.endswith(".glb"):
            kind, fmt, cv = "mesh_group_glb", "glb", MESH_CONTRACT_VERSION
        elif key.endswith("structures.json"):
            kind, fmt, cv = "structures_manifest", "json", MESH_CONTRACT_VERSION
        else:
            kind, fmt, cv = "thumbnail", "png", None
        _upsert_gold_by_key(
            session,
            series_id=series_id,
            kind=kind,
            format_=fmt,
            object_key=key,
            data=data,
            run_id=run.id,
            contract_version=cv,
        )
    session.flush()
    return SeriesStructuresExportResult(
        series_id=series_id,
        manifest_object_key=f"{prefix}/mesh/structures.json",
        group_object_keys=sorted(k for k in outputs if k.endswith(".glb")),
        total_bytes=sum(len(d) for d in outputs.values()),
    )
