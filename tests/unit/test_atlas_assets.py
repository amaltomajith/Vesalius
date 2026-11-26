"""The full-body atlas shipped in assets/atlas/ (built by scripts/build_atlas.py).

The licence guarantee is checked on the SHIPPED BYTES with a list written here by hand, not with
the builder's own rules: parts of the Holoscan atlas under non-commercial or unstated licences
(LICENSES.md) must not be in any GLB. Also: the manifest matches the files exactly, and it uses
the node names three.js will give the meshes (the viewer's selection keys on them).
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any

import pytest

ATLAS = Path(__file__).parents[2] / "assets" / "atlas"
SYSTEMS = [
    "skeletal",
    "cardiovascular",
    "visceral",
    "muscular",
    "nervous",
    "lymphoid",
    "joints",
    "regions",
]

# written by hand from the atlas licence (refs/3D/PC/Resources/Models/License.txt in Holoscan)
MUST_NOT_SHIP = {
    # "Kidney" (CC-BY-NC)
    "Kidney.l",
    "Kidney.r",
    "Renal pelvis.l",
    "Renal pelvis.r",
    "Intrarenal arteries of left kidney",
    "Intrarenal arteries of right kidney",
    "Intrarenal veins of left kidney",
    "Intrarenal veins of right kidney",
    # "Anatomy of the Inner Ear" (CC-BY-NC-SA)
    "Cochlea.l",
    "Cochlea.r",
    "Vestibule.l",
    "Vestibule.r",
    "Internal ear.g",
    "Incus.l",
    "Incus.r",
    "Malleus.l",
    "Malleus.r",
    "Stapes.l",
    "Stapes.r",
    "Tympanic membrane.l",
    "Tympanic membrane.r",
    # "Brainder" / "White matter" (licence not stated)
    "Telencephalon.g",
    "White matter of telencephalon.l",
    "White matter of telencephalon.r",
    "Superior frontal gyrus.l",
    "Precuneus.r",
    "Corpus callosum",
    "Insula.j",
    "White matter of spinal cord",
    "White matter of spinal cord.g",
    "Lateral corticospinal tract",
    "White matter of diencephalon.g",
    "Posterior commissure",
}
# Destrieux cortical parcel names (Brainder) carry "*" or FreeSurfer-style tokens
BRAINDER_MARKERS = re.compile(r"\*|interm_prim-Jensen")
_RESERVED = re.compile(r"[\[\]\.:/]")


def _glb_json(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    magic, version, _ = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and version == 2, path.name
    length, kind = struct.unpack_from("<II", data, 12)
    assert kind == 0x4E4F534A
    return json.loads(data[20 : 20 + length])


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return json.loads((ATLAS / "atlas.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("gid", SYSTEMS)
def test_no_excluded_part_ships(gid: str) -> None:
    names = {n.get("name", "") for n in _glb_json(ATLAS / f"{gid}.glb")["nodes"]}
    assert not names & MUST_NOT_SHIP, sorted(names & MUST_NOT_SHIP)
    assert not [n for n in names if BRAINDER_MARKERS.search(n)]


def test_the_manifest_matches_the_files_it_lists(manifest: dict[str, Any]) -> None:
    assert manifest["kind"] == "atlas" and manifest["deface"]["required"] is False
    assert manifest["geometry"]["export_frame"] == "glTF_Y_up_m"
    assert [g["id"] for g in manifest["groups"]] == SYSTEMS
    for g in manifest["groups"]:
        blob = (ATLAS / g["file"]).read_bytes()
        assert len(blob) == g["bytes"] and hashlib.sha256(blob).hexdigest() == g["sha256"], g["id"]


@pytest.mark.parametrize("gid", SYSTEMS)
def test_structures_use_the_names_three_js_gives_the_meshes(
    gid: str, manifest: dict[str, Any]
) -> None:
    gltf = _glb_json(ATLAS / f"{gid}.glb")
    mesh_nodes = {
        _RESERVED.sub("", re.sub(r"\s", "_", n["name"]))
        for n in gltf["nodes"]
        if "mesh" in n and n.get("name")
    }
    group = next(g for g in manifest["groups"] if g["id"] == gid)
    listed = {s["node"] for s in group["structures"]}
    assert listed == mesh_nodes


def test_descriptions_belong_to_shipped_structures(manifest: dict[str, Any]) -> None:
    descriptions = json.loads((ATLAS / "descriptions.json").read_text(encoding="utf-8"))
    wanted = {s["describe"] for g in manifest["groups"] for s in g["structures"]}
    assert len(descriptions) > 1000 and set(descriptions) <= wanted
    assert "femur" in descriptions and "thigh" in descriptions["femur"].lower()


def test_the_atlas_is_credited_under_its_share_alike_licence() -> None:
    credits = (ATLAS / "CREDITS.txt").read_text(encoding="utf-8")
    assert "BodyParts3D" in credits and "Z-Anatomy" in credits and "CC-BY-SA 4.0" in credits
    excluded = (ATLAS / "EXCLUDED.md").read_text(encoding="utf-8")
    for part in ("Telencephalon.g", "Internal ear.g", "Kidney.l"):
        assert part in excluded
