"""The demo endpoints through FastAPI's TestClient, with the runner faked (no database).

Routes that read the catalog or gold (`/demo/files`, `/demo/report`) need the live stack and are
covered in tests/integration/test_demo_live.py.
"""

from __future__ import annotations

import io
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

from api import demo_state
from api.main import create_app
from api.routers import demo as demo_router
from pipeline.demo.proofs import ProofRunner
from pipeline.demo.runner import PipelineRunner
from tests.unit.test_demo_runner import Fake


@pytest.fixture
def fake() -> Fake:
    return Fake()


@pytest.fixture
def client(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    lock = threading.Lock()
    monkeypatch.setattr(demo_state, "HEAVY_LOCK", lock)
    monkeypatch.setattr(demo_state, "RUNNER", PipelineRunner(fake.deps, lock=lock))
    monkeypatch.setattr(demo_state, "PROOF_RUNNER", ProofRunner(lock=lock))
    monkeypatch.setattr(demo_state, "LAST_PROOF", {})
    return TestClient(create_app())


def _wait_finished(client: TestClient, run_id: str) -> dict:  # type: ignore[type-arg]
    for _ in range(400):
        body = client.get(f"/demo/runs/{run_id}").json()
        if body["state"] == "finished":
            return body  # type: ignore[no-any-return]
        threading.Event().wait(0.01)
    raise AssertionError("run did not finish")


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.dcm", b"x")
    return buf.getvalue()


def test_the_pages_and_their_assets_are_served(client: TestClient) -> None:
    landing = client.get("/")
    assert landing.status_code == 200 and 'href="/app/"' in landing.text
    assert landing.text.count("<canvas") == 1 and 'id="hall-canvas"' in landing.text
    # the console became the Vesalius app: its old address redirects there
    console = client.get("/console", follow_redirects=False)
    assert console.status_code == 308 and console.headers["location"] == "/app/pipeline"
    for asset in (
        "base.css",
        "landing.css",
        "landing.js",
        "icons.svg",
        "fonts/Geist-Variable.woff2",
        "fonts/GeistMono-Variable.woff2",
        "fonts/PlayfairDisplay-Italic.woff2",
        "hall-scene.js",
        "scan/head_points.bin",
        "scan/head_points.json",
        "body/body.bin",
        "body/body.json",
        "body/CREDITS.txt",
        "vendor/three.module.min.js",
        "vendor/three.core.js",
    ):
        assert client.get(f"/static/{asset}").status_code == 200, asset
    assert client.get("/docs").status_code == 200  # the developer docs are still there
    assert client.get("/health").json() == {"status": "ok"}


def test_pages_and_assets_are_revalidated_so_an_update_is_never_hidden_by_a_stale_cache(
    client: TestClient,
) -> None:
    for path in ("/", "/static/landing.css", "/static/scan/head_points.bin"):
        assert client.get(path).headers.get("cache-control") == "no-cache", path
    # revalidation stays cheap: an unchanged file answers 304 to its own ETag
    first = client.get("/static/base.css")
    again = client.get("/static/base.css", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304


def test_the_dataset_catalogue_is_in_plain_language(client: TestClient) -> None:
    body = client.get("/demo/datasets").json()
    keys = [d["key"] for d in body["datasets"]]
    assert keys == ["head", "chest", "rejected"]
    for d in body["datasets"]:
        assert d["title"] and d["blurb"] and d["shows"] and d["approx_seconds"] > 0
    assert [p["key"] for p in body["proofs"]] == [
        "privacy",
        "face",
        "rebuild",
        "contract",
        "viewer",
    ]
    assert body["upload"]["max_megabytes"] == 200 and body["busy"] is False


def test_a_sample_scan_runs_to_completion(client: TestClient, fake: Fake) -> None:
    started = client.post("/demo/runs/sample", json={"dataset": "head"})
    assert started.status_code == 200
    run = _wait_finished(client, started.json()["run_id"])
    assert (
        run["outcome"] == "success"
        and run["title"] == "Head scan"
        and run["preset"] == "skin_surface"
    )
    assert len(run["planted"]) == 8  # the fake identifiers are reported to the page
    assert fake.calls[:2] == ["publish", "receive"]


def test_the_chest_uses_the_bone_preset_and_the_rejected_sample_has_nothing_planted(
    client: TestClient,
) -> None:
    chest = _wait_finished(
        client, client.post("/demo/runs/sample", json={"dataset": "chest"}).json()["run_id"]
    )
    assert chest["preset"] == "bone"
    rejected = _wait_finished(
        client, client.post("/demo/runs/sample", json={"dataset": "rejected"}).json()["run_id"]
    )
    assert rejected["planted"] == []


def test_an_unknown_sample_is_a_plain_400(client: TestClient) -> None:
    r = client.post("/demo/runs/sample", json={"dataset": "../../etc/passwd"})
    assert r.status_code == 400 and r.json()["detail"] == "Unknown sample scan."


def test_a_second_job_while_one_runs_is_a_polite_409(client: TestClient, fake: Fake) -> None:
    fake.wait_at = "quality"
    first = client.post("/demo/runs/sample", json={"dataset": "head"})
    for _ in range(300):
        if "quality" in fake.calls:
            break
        threading.Event().wait(0.01)
    assert client.get("/demo/datasets").json()["busy"] is True
    second = client.post("/demo/runs/sample", json={"dataset": "chest"})
    assert second.status_code == 409 and "Please wait" in second.json()["detail"]
    assert client.post("/demo/proofs/face").status_code == 409  # proofs share the lock
    fake.release.set()
    _wait_finished(client, first.json()["run_id"])


def test_an_unknown_run_or_proof_is_a_404_with_a_human_message(client: TestClient) -> None:
    assert "No such run" in client.get("/demo/runs/nope").json()["detail"]
    assert client.post("/demo/proofs/nope").status_code == 404
    assert client.get("/demo/proofs/jobs/nope").status_code == 404


def _upload(client: TestClient, **over: object):  # type: ignore[no-untyped-def]
    files = {"file": ("my scan.zip", over.pop("content", _zip_bytes()), "application/zip")}
    data = {"acknowledged": "true", "preset": "bone", **over}
    return client.post("/demo/runs/upload", files=files, data=data)


def test_an_upload_without_the_public_data_confirmation_is_refused(client: TestClient) -> None:
    r = _upload(client, acknowledged="false")
    assert r.status_code == 400 and "public or anonymous" in r.json()["detail"]


def test_upload_validation_messages(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        "bone, skin surface or named structures" in _upload(client, preset="lung").json()["detail"]
    )
    assert "not a valid zip" in _upload(client, content=b"definitely not a zip").json()["detail"]
    bad_name = client.post(
        "/demo/runs/upload",
        files={"file": ("scan.exe", b"x", "application/octet-stream")},
        data={"acknowledged": "true"},
    )
    assert bad_name.status_code == 400 and ".zip" in bad_name.json()["detail"]
    monkeypatch.setattr(demo_router, "MAX_UPLOAD_BYTES", 10)
    big = _upload(client, content=b"x" * 100)
    assert big.status_code == 413 and "too large" in big.json()["detail"]


def test_a_valid_upload_runs_and_never_shows_the_file_name(client: TestClient) -> None:
    r = _upload(client)
    assert r.status_code == 200
    run = _wait_finished(client, r.json()["run_id"])
    assert run["title"] == "Your uploaded scan" and "my scan" not in repr(run)
    assert run["dataset"] == "upload" and run["preset"] == "bone"


def test_a_failed_upload_leaves_no_temp_files_behind(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import glob
    import tempfile

    before = set(glob.glob(f"{tempfile.gettempdir()}/demo_*"))
    _upload(client, content=b"definitely not a zip")
    monkeypatch.setattr(demo_router, "MAX_UPLOAD_BYTES", 10)
    _upload(client, content=b"x" * 100)
    assert set(glob.glob(f"{tempfile.gettempdir()}/demo_*")) == before


def test_the_proof_image_is_404_until_the_face_proof_has_produced_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from pathlib import Path

    from pipeline.demo import proofs

    monkeypatch.setattr(proofs, "PROOF_IMAGE", Path(str(tmp_path)) / "missing.png")
    assert client.get("/demo/proofs/image").status_code == 404
