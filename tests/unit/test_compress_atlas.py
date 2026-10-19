"""scripts/compress_atlas.py: the site's atlas is smaller, and the viewer cannot tell otherwise.

The viewer keys entirely on GLB node names and their hierarchy (CLAUDE.md, "node names surviving
export"); a compressor that renames, wraps or moves a node must stop the build, not ship.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import struct
from pathlib import Path
from typing import Any

import pytest

_path = Path(__file__).parents[2] / "scripts" / "compress_atlas.py"
_spec = importlib.util.spec_from_file_location("compress_atlas", _path)
assert _spec and _spec.loader
ca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ca)

SCENE: dict[str, Any] = {
    "asset": {"version": "2.0"},
    "scenes": [{"nodes": [0]}],
    "nodes": [
        {"name": "Skull.g", "children": [1, 2], "scale": [5.0, 5.0, 5.0]},
        {"name": "Ethmoid bone", "mesh": 0, "children": [2]},
        {"name": "Sinus", "mesh": 1, "translation": [0.0, 0.0, 2e-6]},
    ],
    "meshes": [{"name": "Ethmoid bone"}, {"name": "Sinus"}],
    "materials": [{"name": "bone"}],
}


def glb(doc: dict[str, Any], payload: bytes = b"") -> bytes:
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + payload
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def test_an_unchanged_scene_graph_passes_rounding_included() -> None:
    after = copy.deepcopy(SCENE)
    del after["nodes"][2]["translation"]  # ~2e-6 rounded to zero by the encoder
    assert ca.structure_differences(SCENE, after) == []


def test_a_renamed_node_fails() -> None:
    after = copy.deepcopy(SCENE)
    after["nodes"][1]["name"] = "ethmoid"
    assert ca.structure_differences(SCENE, after)


def test_a_wrapper_node_fails() -> None:
    # what meshopt's quantization did: the mesh moved into a new unnamed child node
    after = copy.deepcopy(SCENE)
    after["nodes"][1].pop("mesh")
    after["nodes"][1]["children"] = [2, 3]
    after["nodes"].append({"mesh": 0, "scale": [0.02, 0.02, 0.02]})
    assert ca.structure_differences(SCENE, after)


def test_a_reparented_node_fails() -> None:
    # same names, same meshes; only the hierarchy moved (selection walks it)
    after = copy.deepcopy(SCENE)
    after["nodes"][1]["children"] = []
    assert any("children" in p for p in ca.structure_differences(SCENE, after))


def test_a_moved_node_fails() -> None:
    after = copy.deepcopy(SCENE)
    after["nodes"][2]["translation"] = [0.0, 0.01, 0.0]
    assert any("translation" in p for p in ca.structure_differences(SCENE, after))


def _atlas(tmp_path: Path) -> Path:
    src = tmp_path / "atlas"
    src.mkdir()
    (src / "skull.glb").write_bytes(glb(SCENE, b"x" * 400))
    (src / "CREDITS.txt").write_text("Z-Anatomy, CC-BY-SA")
    manifest = {
        "kind": "atlas",
        "groups": [{"id": "skull", "file": "skull.glb", "bytes": 1, "sha256": "x"}],
    }
    (src / "atlas.json").write_text(json.dumps(manifest))
    return src


def test_compresses_copies_the_credits_and_updates_the_manifest(tmp_path: Path) -> None:
    src, out = _atlas(tmp_path), tmp_path / "out"
    small = glb(SCENE, b"y" * 40)

    sizes = ca.compress_atlas(src, out, compress=lambda a, b: b.write_bytes(small))
    assert sizes == [("skull.glb", len(glb(SCENE, b"x" * 400)), len(small))]
    group = json.loads((out / "atlas.json").read_text())["groups"][0]
    assert (group["bytes"], group["sha256"]) == (len(small), hashlib.sha256(small).hexdigest())
    assert (out / "CREDITS.txt").read_text() == "Z-Anatomy, CC-BY-SA"  # the licence travels with it
    assert (src / "skull.glb").read_bytes() == glb(
        SCENE, b"x" * 400
    )  # the repository's copy is untouched


def test_refuses_and_leaves_nothing_when_the_graph_changes(tmp_path: Path) -> None:
    src, out = _atlas(tmp_path), tmp_path / "out"
    renamed = copy.deepcopy(SCENE)
    renamed["nodes"][2]["name"] = "Frontal sinus"
    with pytest.raises(ValueError, match="scene graph"):
        ca.compress_atlas(src, out, compress=lambda a, b: b.write_bytes(glb(renamed)))
    assert not out.exists()


def test_the_real_atlas_has_nothing_the_check_would_miss() -> None:
    # every node the viewer could select has a name: a wrapper node would show up as a new one
    atlas = Path(__file__).parents[2] / "assets" / "atlas"
    for group in json.loads((atlas / "atlas.json").read_text(encoding="utf-8"))["groups"]:
        doc = ca.gltf_json((atlas / group["file"]).read_bytes())
        assert all(n.get("name") for n in doc["nodes"] if "mesh" in n), group["file"]
