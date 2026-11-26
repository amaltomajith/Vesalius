"""Structures mode (S8-S11) against the real catalog and object store, with a fake engine.

The engine is faked so this runs in seconds anywhere; the real TotalSegmentator engine is
exercised by live runs inside the api image. What is real here is everything the pipeline owns:
the defacing gate, transform runs, idempotency, silver/gold writes, tamper checks and the gold
layout the viewer consumes.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from uuid import UUID

import numpy as np
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library
from sqlalchemy import select

from pipeline.catalog.models import Artifact, Quarantine, TransformRun
from pipeline.common.config import get_settings
from pipeline.stages.s7_deface.service import deface_series
from pipeline.stages.s8_segment.structures import EngineOutput
from pipeline.stages.s8_segment.structures_service import segment_structures_series
from pipeline.stages.s9_surface_extraction.structures_service import extract_structure_surfaces
from pipeline.stages.s10_mesh_postprocess.structures_service import postprocess_structures
from pipeline.stages.s11_export.group_glb import glb_node_names
from pipeline.stages.s11_export.structures_export import export_structures
from pipeline.storage.object_store import get_bytes, put_bytes
from tests.fixtures.shared import integration
from tests.integration.test_s11_export_pipeline import _seed_head


class DensityEngine:
    """Labels a volume by HU: bone -> `skull` (91), soft tissue -> `brain` (90). Same grid."""

    NAMES = {90: "brain", 91: "skull"}

    def describe(self) -> dict[str, object]:
        return {
            "engine": "fake-density",
            "engine_version": "test",
            "task": "total",
            "fast": True,
            "weights_sha256": "0" * 64,
        }

    def segment(self, volume_nii_gz: bytes) -> EngineOutput:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "in.nii.gz"
            p.write_bytes(volume_nii_gz)
            img = sitk.ReadImage(str(p))
        hu = sitk.GetArrayFromImage(img)
        labels = np.zeros(hu.shape, np.int32)
        labels[(hu > -50) & (hu < 150)] = 90
        labels[hu >= 300] = 91
        return EngineOutput(
            labels=labels,
            names=dict(self.NAMES),
            geometry=(img.GetOrigin(), img.GetSpacing(), img.GetDirection()),
        )


def _chain(session, silver, gold, settings, sid: UUID):  # type: ignore[no-untyped-def]
    s8 = segment_structures_series(session, silver, settings, sid, engine=DensityEngine())
    s9 = extract_structure_surfaces(session, silver, settings, sid)
    s10 = postprocess_structures(session, silver, settings, sid)
    s11 = export_structures(session, silver, gold, settings, sid)
    return s8, s9, s10, s11


def _gold(session, sid: UUID) -> dict[str, Artifact]:  # type: ignore[no-untyped-def]
    return {
        a.object_key: a
        for a in session.execute(
            select(Artifact).where(Artifact.series_id == sid, Artifact.zone == "gold")
        ).scalars()
    }


@integration
def test_a_defaced_head_goes_through_structures_mode_into_gold(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    assert not deface_series(pipeline_session, silver_minio_client, settings, sid).quarantined
    s8, s9, s10, s11 = _chain(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid
    )
    assert s8.structure_count == 2 and s9.meshed_count == 2 and not s11.skipped, (s8, s9, s11)

    gold = _gold(pipeline_session, sid)
    manifest_key = f"{sid}/mesh/structures.json"
    assert {f"{sid}/mesh/skeletal.glb", f"{sid}/mesh/nervous.glb", manifest_key} <= set(gold)
    manifest = json.loads(get_bytes(gold_minio_client, settings.minio_gold_bucket, manifest_key))
    assert manifest["deface"]["required"] is True and manifest["deface"]["applied"] is True
    assert manifest["geometry"]["shared_origin"] is True
    assert manifest["deface"]["structures_cut"] == "s7_region_v1"
    # no published vertex of any structure lies inside S7's face region (user decision:
    # bone is cut too). glTF metres -> RAS mm with the manifest's own offset.
    s7 = pipeline_session.execute(
        select(Artifact).where(Artifact.series_id == sid, Artifact.kind == "deface_report")
    ).scalar_one()
    region = json.loads(
        get_bytes(silver_minio_client, settings.minio_silver_bucket, s7.object_key)
    )["region"]["world_mm"]
    offset = np.array(manifest["geometry"]["centring_offset_mm"])
    gltf_to_ras = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]).T  # inverse of RAS_TO_GLTF
    import io

    import trimesh

    for group in manifest["groups"]:
        glb = get_bytes(gold_minio_client, settings.minio_gold_bucket, f"{sid}/{group['file']}")
        for geom in trimesh.load(io.BytesIO(glb), file_type="glb").geometry.values():
            ras = (np.asarray(geom.vertices) / 0.001) @ gltf_to_ras.T - offset
            inside = (ras[:, 1] >= region["a_min"] + 2.0) & (ras[:, 2] >= region["s_min"] + 2.0)
            assert not inside.any(), f"{int(inside.sum())} vertices inside the face region"
    for group in manifest["groups"]:
        glb = get_bytes(gold_minio_client, settings.minio_gold_bucket, f"{sid}/{group['file']}")
        assert glb_node_names(glb) == sorted(s["node"] for s in group["structures"])
    runs = (
        pipeline_session.execute(
            select(TransformRun.stage).where(
                TransformRun.subject_id == sid,
                TransformRun.status == "success",
                TransformRun.parameters["algorithm"]["mode"].astext == "structures",
            )
        )
        .scalars()
        .all()
    )
    assert sorted(runs) == ["S10", "S11", "S8", "S9"]


@integration
def test_a_head_without_a_defacing_run_never_reaches_a_structure_mesh(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)  # no S7 run
    s8, s9, s10, s11 = _chain(
        pipeline_session, silver_minio_client, gold_minio_client, settings, sid
    )
    assert s8.skipped and s8.gate_reason_code is not None
    assert s9.skipped and s10.skipped and s11.skipped
    assert not [k for k in _gold(pipeline_session, sid) if "/mesh/" in k]


@integration
def test_a_tampered_label_file_is_quarantined_not_used(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    s8 = segment_structures_series(
        pipeline_session, silver_minio_client, settings, sid, engine=DensityEngine()
    )
    assert s8.labels_object_key
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        s8.labels_object_key,
        b"not the labels",
        "application/gzip",
    )
    s9 = extract_structure_surfaces(pipeline_session, silver_minio_client, settings, sid)
    assert s9.quarantined and s9.reason_code == "S9_STRUCTURES_INPUT_TAMPERED"
    assert (
        pipeline_session.execute(
            select(Quarantine).where(
                Quarantine.subject_id == sid,
                Quarantine.reason_code == "S9_STRUCTURES_INPUT_TAMPERED",
            )
        ).scalar_one_or_none()
        is not None
    )


@integration
def test_a_rerun_reuses_the_recorded_structures_runs(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    deface_series(pipeline_session, silver_minio_client, settings, sid)
    first = _chain(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    n_runs = len(
        pipeline_session.execute(
            select(TransformRun.id).where(TransformRun.subject_id == sid)
        ).all()
    )
    second = _chain(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    assert (
        len(
            pipeline_session.execute(
                select(TransformRun.id).where(TransformRun.subject_id == sid)
            ).all()
        )
        == n_runs
    )
    assert first[0].labels_object_key == second[0].labels_object_key
    assert first[3].group_object_keys == second[3].group_object_keys
