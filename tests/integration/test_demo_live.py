"""The demo interface against the REAL system: the real runner over live Postgres + MinIO, driven
through the same HTTP routes the page uses.

OCR is stubbed (no tesseract on a bare host; the OCR channel itself is proven in Docker by Proof 1).
Everything else -- S1..S11, the gate, gold, lineage, the S12 endpoints -- is real.
"""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Iterator
from unittest import mock

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from api import demo_state
from api.main import create_app
from pipeline.catalog.models import Artifact, Quarantine
from pipeline.catalog.session import get_pipeline_session
from pipeline.common.config import get_settings
from pipeline.demo import datasets
from pipeline.demo.runner import PipelineRunner, real_deps
from pipeline.stages.s11_export.glb_io import load_glb
from tests.fixtures.shared import integration, require_integration


@pytest.fixture(scope="module", autouse=True)
def no_ocr() -> Iterator[None]:
    require_integration()
    with (
        mock.patch(
            "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions", lambda i: []
        ),
        mock.patch("pipeline.stages.s5_verify.pixel_verify.detect_text_regions", lambda i: []),
    ):
        yield


@pytest.fixture(scope="module")
def client() -> TestClient:
    require_integration()
    lock = threading.Lock()
    demo_state.HEAVY_LOCK = lock
    demo_state.RUNNER = PipelineRunner(real_deps, lock=lock)
    return TestClient(create_app())


def _run_sample(client: TestClient, key: str) -> dict:  # type: ignore[type-arg]
    started = client.post("/demo/runs/sample", json={"dataset": key})
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    for _ in range(1200):
        run = client.get(f"/demo/runs/{run_id}").json()
        if run["state"] == "finished":
            return run  # type: ignore[no-any-return]
        threading.Event().wait(0.25)
    raise AssertionError("the run did not finish in 5 minutes")


@pytest.fixture(scope="module")
def head(client: TestClient) -> dict:  # type: ignore[type-arg]
    require_integration()
    return _run_sample(client, "head")


@pytest.fixture(scope="module")
def chest(client: TestClient) -> dict:  # type: ignore[type-arg]
    require_integration()
    return _run_sample(client, "chest")


def _artifacts(client: TestClient, series_id: str) -> dict[str, dict]:  # type: ignore[type-arg]
    body = client.get(f"/series/{series_id}/artifacts").json()
    return {a["kind"]: a for a in body["artifacts"]}


# ---- the head: the story the page tells -------------------------------------------------


@integration
def test_the_head_sample_completes_every_step(head: dict) -> None:  # type: ignore[type-arg]
    assert head["outcome"] == "success", head["message"]
    assert [s["state"] for s in head["steps"]] == ["done"] * 10
    assert len(head["series_ids"]) == 1 and head["batch_id"]
    assert len(head["planted"]) == 8
    details = {s["key"]: s["detail"] for s in head["steps"]}
    assert "106 image files" in details["receive"]
    assert "Face removed" in details["face"]


@integration
def test_the_head_is_catalogued_as_needing_and_receiving_face_removal(
    client: TestClient,
    head: dict,  # type: ignore[type-arg]
) -> None:
    sid = head["series_ids"][0]
    series = client.get(f"/series/{sid}").json()
    assert series["deface_required"] is True and series["body_part"] == "HEAD"
    assert series["quality_status"] == "pass" and series["has_gold"] is True
    stages = {n["stage"] for n in client.get(f"/series/{sid}/lineage").json()["nodes"]}
    assert stages == {f"S{i}" for i in range(1, 12)}  # the whole chain, ingest to export


@integration
def test_the_exported_head_model_has_no_face(client: TestClient, head: dict) -> None:  # type: ignore[type-arg]
    """The undefaced head is ~211 mm front to back (nose tip at +122, back of skull at -89);
    with the face removed the model is ~177 mm. Measured on the GLB the browser displays."""
    sid = head["series_ids"][0]
    glb_id = _artifacts(client, sid)["surface_glb"]["id"]
    served = client.get(f"/demo/files/{glb_id}")
    assert served.status_code == 200 and served.headers["content-type"] == "model/gltf-binary"
    vertices, faces, _, _ = load_glb(served.content)
    ap_extent_m = float(np.ptp(vertices[:, 2]))  # glTF: anterior is -Z
    assert 0.15 < ap_extent_m < 0.195, ap_extent_m
    assert len(faces) <= 110_000  # the demo's size budget


@integration
def test_files_are_served_from_gold_only_and_can_be_downloaded(
    client: TestClient,
    head: dict,  # type: ignore[type-arg]
) -> None:
    sid = head["series_ids"][0]
    arts = _artifacts(client, sid)
    for kind, media in (("surface_glb", "model/gltf-binary"), ("thumbnail", "image/png")):
        r = client.get(f"/demo/files/{arts[kind]['id']}")
        assert r.status_code == 200 and r.headers["content-type"] == media and r.content
    dl = client.get(f"/demo/files/{arts['surface_glb']['id']}?download=1")
    assert 'attachment; filename="surface.glb"' in dl.headers["content-disposition"]

    with get_pipeline_session(get_settings()) as session:
        silver = session.execute(
            select(Artifact).where(Artifact.series_id == sid, Artifact.zone == "silver")
        ).scalars()
        silver_ids = [str(a.id) for a in silver]
    assert silver_ids
    for artifact_id in silver_ids[:3] + ["00000000-0000-0000-0000-000000000000", "not-a-uuid"]:
        assert client.get(f"/demo/files/{artifact_id}").status_code == 404


@integration
def test_the_report_is_a_standalone_page_with_pictures(client: TestClient, head: dict) -> None:  # type: ignore[type-arg]
    r = client.get(f"/demo/report/{head['id']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "Head scan" in html and "Completed" in html and "face removal required" in html
    assert html.count("data:image/png;base64,") == 2  # the model preview and the slice
    assert "<script" not in html and "http://" not in html and "https://" not in html
    assert "DEMO^SYNTHETIC^PATIENT" in html  # the planted fake identifiers are listed, as fakes
    assert "Fake identifiers planted" in html


@integration
def test_the_report_for_an_unknown_run_is_a_404(client: TestClient) -> None:
    assert client.get("/demo/report/nope").status_code == 404


# ---- the chest: no face removal, and the record says why --------------------------------


@integration
def test_the_chest_needs_no_face_removal_and_the_record_explains_why(
    client: TestClient,
    chest: dict,  # type: ignore[type-arg]
) -> None:
    assert chest["outcome"] == "success", chest["message"]
    face = next(s for s in chest["steps"] if s["key"] == "face")
    assert face["state"] == "done" and "No face removal needed" in face["detail"]
    sid = chest["series_ids"][0]
    series = client.get(f"/series/{sid}").json()
    assert series["deface_required"] is False and series["body_part"] == "CHEST"
    report = client.get(f"/demo/report/{chest['id']}").text
    assert "face removal not required" in report


# ---- the rejected scan ------------------------------------------------------------------


@integration
def test_a_scan_with_one_image_is_rejected_explained_and_recorded(client: TestClient) -> None:
    run = _run_sample(client, "rejected")
    assert run["outcome"] == "rejected"
    states = {s["key"]: s["state"] for s in run["steps"]}
    assert states["receive"] == states["catalog"] == "done"
    assert states["quality"] == "rejected"
    assert all(
        states[k] == "skipped"
        for k in ("privacy", "volume", "face", "segment", "surface", "smooth", "package")
    )
    assert "fewer than 3 images" in run["message"] and "Quality check" in run["message"]
    assert not any(code in run["message"] for code in ("QG_", "S3_"))  # never a bare code
    assert run["series_ids"] == []

    # "quarantine, never drop": the rejection is a durable, queryable record
    with get_pipeline_session(get_settings()) as session:
        codes = set(
            session.execute(
                select(Quarantine.reason_code).where(Quarantine.stage == "S3")
            ).scalars()
        )
    assert "QG_TOO_FEW_SLICES" in codes


# ---- upload -----------------------------------------------------------------------------


@integration
def test_an_uploaded_zip_runs_through_the_same_pipeline(client: TestClient) -> None:
    built = datasets.build("chest")
    r = client.post(
        "/demo/runs/upload",
        files={"file": ("some patient name.zip", built.zip_bytes, "application/zip")},
        data={"acknowledged": "true", "preset": "bone"},
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    for _ in range(600):
        run = client.get(f"/demo/runs/{run_id}").json()
        if run["state"] == "finished":
            break
        threading.Event().wait(0.25)
    assert run["outcome"] == "success" and run["title"] == "Your uploaded scan"
    assert "some patient name" not in repr(run)
    assert zipfile.is_zipfile(io.BytesIO(built.zip_bytes))
