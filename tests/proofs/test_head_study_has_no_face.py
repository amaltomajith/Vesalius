"""Proof 5 (refs/INTEGRATION.md §7, step 9): load a head study the way the viewer loads it,
confirm defacing was applied, and confirm no published mesh has a face.

  A. Real stages on the real stack: S7 defaces a head, structures mode S8-S11 runs (a fake
     engine; the real TotalSegmentator run is recorded with M2), everything is COMMITTED, and the
     study is then read back ONLY through S12's viewer routes (`/studies`, `/studies/{id}/
     structures`, `/studies/{id}/mesh/{group}.glb`) as `serving_ro`: 307 presigned redirects into
     the gold bucket. The manifest says the face was required, applied and cut from structures;
     no vertex of any served structure lies inside S7's face region, and nothing is near the nose.
  B. Teeth: the same check on the same study WITHOUT structures mode's face cut finds bone in the
     face region, and on the raw (undefaced) volume finds the nose. So A is not vacuous.
  C. The viewer side of the rule ("fail closed in the viewer too", CLAUDE.md rule 4) is proven in
     viewer/src/contract/meshContract.test.ts (vitest, mutation-checked) and by the Playwright run
     recorded with M4: a doctored manifest (required, not applied) is refused and no GLB is ever
     requested. This image has no Node, so it is not re-run here.

Same caveat as every phantom test in this project: the synthetic head shows the mechanism, not
that defacing is effective on every real anatomy. The real head CT on the landing page and in the
viewer went through the same code (see M2/M4 notes).
"""

from __future__ import annotations

import io
import json
from urllib.parse import urlparse
from uuid import UUID

import numpy as np
import trimesh
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from api.main import create_app
from pipeline.catalog.contracts import publish_all
from pipeline.catalog.models import Artifact, LineageEdge
from pipeline.catalog.session import get_pipeline_session
from pipeline.common.config import Settings, get_settings
from pipeline.stages.s7_deface.gate import open_defacing_gate
from pipeline.stages.s7_deface.service import deface_series
from pipeline.stages.s8_segment.structures_service import segment_structures_series
from pipeline.stages.s9_surface_extraction import structure_meshing as sm
from pipeline.stages.s9_surface_extraction.structures_service import extract_structure_surfaces
from pipeline.stages.s10_mesh_postprocess.structures_service import postprocess_structures
from pipeline.stages.s11_export.structures_export import export_structures
from pipeline.storage.object_store import get_bytes, gold_client, silver_client
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import phantom_nifti
from tests.fixtures.shared import integration
from tests.integration.test_s11_export_pipeline import BASE, _seed_head
from tests.integration.test_structures_pipeline import DensityEngine

GLTF_TO_RAS = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]).T  # inverse of S11's RAS_TO_GLTF
NOSE_CENTRE = np.array(hp.NOSE[0])  # RAS mm
TOL_MM = 2.0  # marching cubes on a blurred mask may sit a fraction of a voxel past a hard plane


def _in_face(ras: np.ndarray, region: dict[str, float]) -> np.ndarray:
    return (ras[:, 1] >= region["a_min"] + TOL_MM) & (ras[:, 2] >= region["s_min"] + TOL_MM)


def _served_key(client: TestClient, path: str, settings: Settings) -> str:
    """Follows one viewer route to its presigned redirect and returns the gold object key it
    names. Anything but a 307 into the gold bucket fails the proof."""
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 307, (path, r.status_code, r.text[:200])
    url = urlparse(r.headers["location"])
    bucket, _, key = url.path.lstrip("/").partition("/")
    assert bucket == settings.minio_gold_bucket, f"{path} redirected outside gold: {bucket}"
    assert "X-Amz-Signature" in url.query, "not a presigned URL"
    return key


def _withdraw_from_serving(sid: UUID) -> None:
    """This proof must COMMIT (the API reads as serving_ro), so its synthetic head would stay
    listed by GET /studies in the demo's viewer. Afterwards it withdraws its own study from
    serving: the gold structures rows go (nothing downstream uses them); the gold bytes stay,
    because the gold credential cannot delete (a documented finding), and are unreachable."""
    kinds = ("structures_manifest", "mesh_group_glb")
    with get_pipeline_session(get_settings()) as session:
        ids = select(Artifact.id).where(
            Artifact.series_id == sid, Artifact.zone == "gold", Artifact.kind.in_(kinds)
        )
        session.execute(delete(LineageEdge).where(LineageEdge.upstream_artifact.in_(ids)))
        session.execute(delete(Artifact).where(Artifact.id.in_(ids)))


def _vertices_ras(glb: bytes, offset_mm: np.ndarray) -> dict[str, np.ndarray]:
    scene = trimesh.load(io.BytesIO(glb), file_type="glb")
    assert isinstance(scene, trimesh.Scene)
    return {
        name: (np.asarray(g.vertices) / 0.001) @ GLTF_TO_RAS.T - offset_mm
        for name, g in scene.geometry.items()
    }


@integration
def test_proof5_a_head_study_served_to_the_viewer_has_its_face_removed() -> None:
    settings = get_settings()
    silver, gold = silver_client(settings), gold_client(settings)
    with get_pipeline_session(settings) as session:
        publish_all(session)
        sid: UUID = _seed_head(session, silver, settings)
        assert not deface_series(session, silver, settings, sid).quarantined
        s8 = segment_structures_series(session, silver, settings, sid, engine=DensityEngine())
        s9 = extract_structure_surfaces(session, silver, settings, sid)
        s10 = postprocess_structures(session, silver, settings, sid)
        s11 = export_structures(session, silver, gold, settings, sid)
        assert not (s8.skipped or s9.skipped or s10.skipped or s11.skipped), (s8, s9, s10, s11)
        s7_report = session.execute(
            select(Artifact).where(Artifact.series_id == sid, Artifact.kind == "deface_report")
        ).scalar_one()
        region = json.loads(get_bytes(silver, settings.minio_silver_bucket, s7_report.object_key))[
            "region"
        ]["world_mm"]
        # B (teeth), on the SAME gated volume the pipeline used: labels without the cut
        gated = open_defacing_gate(session, silver, settings, sid)
        uncut = DensityEngine().segment(gated.volume_bytes)
    # everything above is committed: from here on, only the viewer's routes as serving_ro
    try:
        _check_served(sid, region, uncut, gated.volume_bytes, settings)
    finally:
        _withdraw_from_serving(sid)
    client = TestClient(create_app())
    assert str(sid) not in {s["id"] for s in client.get("/studies").json()["studies"]}


def _check_served(  # type: ignore[no-untyped-def]
    sid: UUID, region: dict[str, float], uncut, gated_volume: bytes, settings: Settings
) -> None:
    gold = gold_client(settings)
    client = TestClient(create_app())
    listed = {s["id"]: s for s in client.get("/studies").json()["studies"]}
    assert str(sid) in listed and listed[str(sid)]["body_part"] == "HEAD"

    manifest_key = _served_key(client, f"/studies/{sid}/structures", settings)
    manifest = json.loads(get_bytes(gold, settings.minio_gold_bucket, manifest_key))
    deface = manifest["deface"]
    assert deface["required"] is True and deface["applied"] is True, deface
    assert deface["structures_cut"] == "s7_region_v1", deface
    offset = np.asarray(manifest["geometry"]["centring_offset_mm"], dtype=float)

    served: dict[str, np.ndarray] = {}
    for group in manifest["groups"]:
        key = _served_key(client, f"/studies/{sid}/mesh/{group['id']}.glb", settings)
        served.update(_vertices_ras(get_bytes(gold, settings.minio_gold_bucket, key), offset))
    assert served, "no structure was served"
    for node, ras in served.items():
        inside = int(_in_face(ras, region).sum())
        assert inside == 0, f"{node}: {inside} vertices inside the face region"
    nearest = min(float(np.linalg.norm(v - NOSE_CENTRE, axis=1).min()) for v in served.values())
    assert nearest > 15.0, f"a served vertex is {nearest:.1f} mm from the nose"

    # B. the same check has teeth: without the cut, bone is in the face region ...
    gated_affine = _affine(gated_volume)
    skull_id = next(k for k, v in uncut.names.items() if v == "skull")
    bone, _ = sm.mesh_structure(uncut.labels, skull_id, gated_affine)
    assert int(_in_face(bone, region).sum()) > 0, "control: uncut bone should reach the face"
    # ... and on the raw volume, soft tissue reaches the nose
    raw = DensityEngine().segment(phantom_nifti(BASE))
    tissue_id = next(k for k, v in raw.names.items() if v == "brain")
    skin, _ = sm.mesh_structure(raw.labels, tissue_id, _affine(phantom_nifti(BASE)))
    assert float(np.linalg.norm(skin - NOSE_CENTRE, axis=1).min()) < 15.0, "control: nose"


def _affine(nifti: bytes) -> np.ndarray:
    from pipeline.stages.s7_deface import nifti_io as vol_io

    return np.asarray(vol_io.load_volume(nifti).sform)
