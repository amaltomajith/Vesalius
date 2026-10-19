"""scripts/export_static.py: the static site's data, taken from the gold serving routes only.

The public site has no server, so whatever this writes is published as is. It must fail closed
(write nothing) on an undefaced or uncut head, on a mesh that matches no gold record, on a study
with no public source to credit, and unless proof 5 (the viewer shows no face) last passed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "export_static.py"
_spec = importlib.util.spec_from_file_location("export_static", SCRIPT)
assert _spec and _spec.loader
es = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(es)

HEAD, CHEST = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
# every route the exporter may read; anything else is a test failure (gold serving surface only)
SERVING = (
    "/studies",
    "/series/",
    "/artifacts/",
    "/demo/proofs/",
    "/demo/datasets",
    "https://files.",
)


def _glb(tag: str) -> bytes:
    return b"glTF" + tag.encode() * 8


def _manifest(sid: str, part: str, **deface: Any) -> dict[str, Any]:
    glb = _glb(sid[:2])
    base = {
        "required": part == "HEAD",
        "applied": part == "HEAD",
        "method": "s7",
        "structures_cut": "s7_region_v1" if part == "HEAD" else None,
    }
    return {
        "contract_version": "1.0",
        "pipeline_version": "0.1.0",
        "deface": {**base, **deface},
        "groups": [{"id": "skeletal", "file": "mesh/skeletal.glb", "bytes": len(glb)}],
    }


class FakeApi:
    def __init__(self) -> None:
        self.manifests = {HEAD: _manifest(HEAD, "HEAD"), CHEST: _manifest(CHEST, "CHEST")}
        self.parts = {HEAD: "HEAD", CHEST: "CHEST"}
        self.glbs = {sid: _glb(sid[:2]) for sid in self.manifests}
        self.gold_glbs = dict(self.glbs)  # what the gold artifact records say
        self.proofs: dict[str, Any] = {
            "viewer": {"verdict": "passed"},
            "face": {"verdict": "passed"},
        }
        self.requested: list[str] = []

    def get(self, path: str) -> bytes:
        self.requested.append(path)
        assert path.startswith(SERVING), f"exporter read outside the serving surface: {path}"
        if path == "/studies":
            studies = [
                {"id": s, "body_part": p, "thumbnail_artifact_id": f"t{s[:2]}"}
                for s, p in self.parts.items()
            ]
            return json.dumps({"studies": studies}).encode()
        if path == "/demo/proofs/last":
            return json.dumps(self.proofs).encode()
        if path == "/demo/proofs/image":
            return b"\x89PNG side by side"
        if path == "/demo/datasets":
            return json.dumps(
                {
                    "datasets": [{"key": "head"}],
                    "proofs": [{"key": "viewer"}],
                    "upload": {"max_megabytes": 200},
                    "busy": True,
                    "latest_run": "r1",
                }
            ).encode()
        if path.startswith("/artifacts/"):
            return json.dumps({"url": f"https://files.example/gold/{path[11:]}.png"}).encode()
        if path.startswith("https://files."):
            return b"\x89PNG thumb"
        sid = path.split("/")[2]
        if path.endswith("/structures"):
            return json.dumps(self.manifests[sid]).encode()
        if path.endswith(".glb"):
            return self.glbs[sid]
        if path.endswith("/lineage"):
            return json.dumps({"nodes": [{"stage": "S11"}]}).encode()
        if path.endswith("/artifacts"):
            body = self.gold_glbs[sid]
            arts = [
                {
                    "bytes": len(body),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "kind": "structure_glb",
                }
            ]
            return json.dumps({"artifacts": arts}).encode()
        raise AssertionError(path)


def test_exports_every_study_with_its_credit_and_the_proofs(tmp_path: Path) -> None:
    api = FakeApi()
    out = tmp_path / "site"
    index = es.export(api.get, out)

    data = out / "app" / "data"
    listed = json.loads((data / "studies.json").read_text())["studies"]
    assert {s["body_part"] for s in listed} == {"HEAD", "CHEST"} == {s["body_part"] for s in index}
    assert all("doi:" in s["credit"] or "3D Slicer" in s["credit"] for s in listed)
    head = data / "studies" / HEAD
    assert (
        json.loads((head / "structures.json").read_text()) == api.manifests[HEAD]
    )  # byte-faithful gold
    assert (head / "mesh" / "skeletal.glb").read_bytes() == api.glbs[HEAD]
    assert (head / "thumb.png").read_bytes() == b"\x89PNG thumb"
    assert "3D Slicer" in (head / "CREDIT.txt").read_text()
    assert json.loads((data / "proofs" / "last.json").read_text())["viewer"]["verdict"] == "passed"
    assert (out / "demo" / "proofs" / "last").read_bytes() == (
        data / "proofs" / "last.json"
    ).read_bytes()
    catalogue = json.loads((data / "datasets.json").read_text())
    assert catalogue["busy"] is False and catalogue["latest_run"] is None  # no live state is frozen


def _refused(api: FakeApi, tmp_path: Path, match: str) -> None:
    out = tmp_path / "site"
    out.mkdir()
    (out / "previous.txt").write_text("the last good export")
    with pytest.raises(es.RefusedError, match=match):
        es.export(api.get, out)
    assert [p.name for p in out.iterdir()] == ["previous.txt"]  # nothing written, nothing removed


def test_refuses_a_head_whose_face_was_not_removed(tmp_path: Path) -> None:
    api = FakeApi()
    api.manifests[HEAD]["deface"]["applied"] = False
    _refused(api, tmp_path, "face removal required")


def test_refuses_a_head_whose_structures_were_not_cut(tmp_path: Path) -> None:
    api = FakeApi()
    api.manifests[HEAD]["deface"]["structures_cut"] = None
    _refused(api, tmp_path, "not cut")


def test_refuses_a_mesh_that_matches_no_gold_record(tmp_path: Path) -> None:
    api = FakeApi()
    api.glbs[CHEST] = _glb("zz")  # same size, different bytes
    _refused(api, tmp_path, "matches no gold artifact")


def test_refuses_a_study_with_no_public_source(tmp_path: Path) -> None:
    api = FakeApi()
    api.parts[CHEST] = "PELVIS"
    _refused(api, tmp_path, "no known public source")


@pytest.mark.parametrize(
    "proofs", [{}, {"viewer": {"verdict": "failed"}}, {"viewer": {"verdict": "incomplete"}}]
)
def test_refuses_unless_proof_5_last_passed(tmp_path: Path, proofs: dict[str, Any]) -> None:
    api = FakeApi()
    api.proofs = proofs
    _refused(api, tmp_path, "proof 'viewer'")


def test_reads_only_the_gold_serving_surface(tmp_path: Path) -> None:
    api = FakeApi()
    es.export(api.get, tmp_path / "site")
    assert api.requested  # FakeApi.get asserts each path; here: nothing bypassed it
    assert not any(p.startswith(("/demo/runs", "/demo/files", "/ingest")) for p in api.requested)
