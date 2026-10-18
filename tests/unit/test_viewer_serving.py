"""The Vesalius app (viewer/, React) as served by the API, and the guarantees its source must keep.

* the built app is served at /app/ on the API's own origin (no CORS, no second server), with
  client-side routes falling back to index.html and the atlas at /app/atlas/;
* the old /console and /viewer/ addresses redirect into it;
* the app never talks to an external host at runtime; any AI call goes through this server,
  which holds the key, and a patient study never sends an image (CLAUDE.md hard rule 8);
* every scene goes through validateMeshContract before anything renders (hard rule 4).
Limits: the source checks are textual scans. Behaviour is covered by viewer/src/**/*.test.ts
(vitest) and the Playwright runs recorded with each milestone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.routers import ui

ROOT = Path(__file__).parents[2]
VIEWER_SRC = ROOT / "viewer" / "src"


@pytest.fixture
def built_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    app = tmp_path / "app"
    (app / "assets").mkdir(parents=True)
    (app / "index.html").write_text("<!doctype html><title>Vesalius</title>", "utf-8")
    (app / "assets" / "index-abc123.js").write_text("console.log(1)", "utf-8")
    (app / "favicon.svg").write_text("<svg/>", "utf-8")
    atlas = tmp_path / "atlas"
    atlas.mkdir()
    (atlas / "atlas.json").write_text("{}", "utf-8")
    (atlas / "skeletal.glb").write_bytes(b"glTF")
    (tmp_path / "secret.txt").write_text("outside", "utf-8")
    monkeypatch.setenv("VESALIUS_APP_DIR", str(app))
    monkeypatch.setenv("VESALIUS_ATLAS_DIR", str(atlas))
    return tmp_path


def test_the_app_is_served_at_app_with_client_routes(built_app: Path) -> None:
    client = TestClient(api_main.create_app())
    root = client.get("/app", follow_redirects=False)
    assert root.status_code == 308 and root.headers["location"] == "/app/"
    for route in ("/app/", "/app/studies", "/app/hologram/abc", "/app/body", "/app/pipeline"):
        page = client.get(route)
        assert page.status_code == 200 and "<title>Vesalius</title>" in page.text, route
        assert page.headers["cache-control"] == "no-cache", route
    asset = client.get("/app/assets/index-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert client.get("/app/assets/missing.js").status_code == 404  # a missing file is not a route


def test_the_atlas_is_served_and_cached_by_content_key(built_app: Path) -> None:
    client = TestClient(api_main.create_app())
    assert client.get("/app/atlas/atlas.json").headers["cache-control"] == "no-cache"
    keyed = client.get("/app/atlas/skeletal.glb?v=0123456789ab")
    assert keyed.status_code == 200 and "immutable" in keyed.headers["cache-control"]
    assert client.get("/app/atlas/nope.glb").status_code == 404


def test_nothing_outside_the_app_or_atlas_can_be_read(built_app: Path) -> None:
    client = TestClient(api_main.create_app())
    for path in (
        "/app/../secret.txt",
        "/app/%2e%2e/secret.txt",
        "/app/atlas/../secret.txt",
        "/app/atlas/%2e%2e/secret.txt",
    ):
        r = client.get(path)
        assert "outside" not in r.text, path


def test_the_old_addresses_redirect_into_the_app(built_app: Path) -> None:
    client = TestClient(api_main.create_app())
    for path, target in (
        ("/console", "/app/pipeline"),
        ("/viewer", "/app/studies"),
        ("/viewer/", "/app/studies"),
        (
            "/viewer/?study=5dc1438a-db6f-49b6-935d-a5e35426febe",
            "/app/hologram/5dc1438a-db6f-49b6-935d-a5e35426febe",
        ),
    ):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 308 and r.headers["location"] == target, path


def test_an_unbuilt_app_is_a_clean_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VESALIUS_APP_DIR", str(tmp_path))  # empty: no index.html
    monkeypatch.setattr(ui, "app_dir", lambda: None)
    assert TestClient(api_main.create_app()).get("/app/").status_code == 404


# ---- source guarantees ------------------------------------------------------------------------


def _app_sources() -> list[Path]:
    assert VIEWER_SRC.is_dir(), "viewer/src missing: these guards must see the app source"
    files = sorted(p for p in VIEWER_SRC.rglob("*") if p.suffix in {".ts", ".tsx", ".css"})
    assert files
    return [p for p in files if not p.name.endswith(".test.ts")]


@pytest.mark.parametrize("path", _app_sources(), ids=lambda p: p.name)
def test_the_app_makes_no_external_request(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"https?://", text), f"{path.name}: absolute URL in the app source"
    assert "@import url(" not in text, f"{path.name}: remote stylesheet import"


@pytest.mark.parametrize("path", _app_sources(), ids=lambda p: p.name)
def test_the_app_holds_no_ai_key_and_calls_no_ai_host(path: Path) -> None:
    text = path.read_text(encoding="utf-8").lower()
    for needle in ("api.groq.com", "vite_groq", "openai.com", "image_url", "authorization"):
        assert needle not in text, f"{path.name}: mentions {needle!r}"


def test_a_patient_study_can_never_send_an_image() -> None:
    ai = (VIEWER_SRC / "services" / "ai.ts").read_text(encoding="utf-8")
    study_req = ai[ai.index("export interface StudyInsightRequest") :]
    study_req = study_req[: study_req.index("}")]
    assert "image" not in study_req
    panel = (VIEWER_SRC / "components" / "AiInsight.tsx").read_text(encoding="utf-8")
    assert "kind: 'study'; node: string; label: string; studyId: string; snapshot: null" in panel
    hologram = (VIEWER_SRC / "screens" / "Hologram.tsx").read_text(encoding="utf-8")
    # the snapshot is decided before any pixel is read (lib/snapshot.ts, since the AI snapshot
    # gesture moved into the gesture engine): not the atlas means no capture, and the frame is only
    # read after that decision allowed it
    decide = hologram.index("const d = decideSnapshot(atlasRef.current")
    refuse = hologram.index("if (!d.capture) { say(d.message); return; }")
    read = hologram.index("setSnapshot(capture());")
    assert decide < refuse < read
    rule = (VIEWER_SRC / "lib" / "snapshot.ts").read_text(encoding="utf-8")
    assert "if (!atlas) return { capture: false" in rule
    assert "snapshot={atlas ? snapshot : null}" in hologram


def test_every_scene_is_validated_before_it_can_render() -> None:
    contract = (VIEWER_SRC / "contract" / "meshContract.ts").read_text(encoding="utf-8")
    assert "m.deface.required && !m.deface.applied" in contract
    # every loader validates before it hands anything back
    for fn in ("async function loadSurface", "export async function loadStudy"):
        body = contract[contract.index(fn) :]
        body = body[: body.index("\n}\n")]
        assert body.count("validateMeshContract(manifest)") >= 1, fn
        assert body.index("validateMeshContract(manifest)") < body.rindex("return "), fn
    store = (VIEWER_SRC / "store" / "studyStore.ts").read_text(encoding="utf-8")
    assert store.index("await loadStudy(s)") < store.index("setFromManifest(")
    others = [
        p.name
        for p in _app_sources()
        if "setFromManifest(" in p.read_text(encoding="utf-8")
        and p.name not in {"studyStore.ts", "layerStore.ts"}
    ]
    assert not others, f"layers set from a manifest outside the validated path: {others}"
