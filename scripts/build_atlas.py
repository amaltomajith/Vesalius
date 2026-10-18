"""Builds the full-body atlas the Vesalius app shows at /app/body (Holoscan's 8 systems, full
detail) into assets/atlas/, so this project runs without the Holoscan folder.

    python scripts/build_atlas.py [HOLOSCAN_DIR]        # default F:/Projects/Holoscan

Pure standard library. Input: HOLOSCAN_DIR/public/models/*.glb (Z-Anatomy, derived from
BodyParts3D) and HOLOSCAN_DIR/refs/3D/PC/Resources/Descriptions/OriginalDescriptions/*.txt.

LICENCE (CLAUDE.md, LICENSES.md): the atlas is CC-BY-SA, but it bundles third-party models under
other licences (refs/3D/PC/Resources/Models/License.txt). Those that are non-commercial or whose
licence is not stated are REMOVED, by whole scene subtrees, before anything is written:
  * "Brainder" and "White matter" (licence not stated): the cerebrum's subtree `Telencephalon.g`
    (the Destrieux cortical parcels, the telencephalic white matter incl. the corpus callosum, and,
    conservatively, the basal nuclei and lateral ventricles that share that subtree);
    Also every other "White matter" subtree (spinal cord tracts, diencephalon; user decision
    2026-09-27: "licence not stated" means it does not ship);
  * "Anatomy of the Inner Ear" (CC-BY-NC-SA): `Internal ear.g`, and conservatively the middle-ear
    parts such a model shows (auditory ossicles, tympanic membrane);
  * "Kidney" (CC-BY-NC): both kidneys, the renal pelvis, the intrarenal vessels and the segmental
    renal branches (renal artery/vein trunks, ureters and suprarenal glands stay).
"Cranial Nerves and Foramina" (CC-BY 4.0) ships, credited.

Everything else is copied BYTE FOR BYTE: the builder edits the glTF JSON (drops the excluded
nodes and the meshes/accessors/buffer views only they used) and rewrites the binary chunk with the
surviving buffer views in their original order. No re-export, no decimation. Deterministic.

Outputs (assets/atlas/): <system>.glb x8, atlas.json (mesh_contract shape, kind "atlas"),
descriptions.json (Z-Anatomy / Wikipedia-derived intros, CC-BY-SA), EXCLUDED.md, licence files.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "atlas"
DEFAULT_HOLOSCAN = Path("F:/Projects/Holoscan")

# (group id, label, source file, visible by default)
SYSTEMS: list[tuple[str, str, str, bool]] = [
    ("skeletal", "Skeletal", "SkeletalSystem100.glb", True),
    ("cardiovascular", "Cardiovascular", "CardioVascular41.glb", True),
    ("visceral", "Visceral", "VisceralSystem100.glb", True),
    ("muscular", "Muscular", "MuscularSystem100.glb", False),
    ("nervous", "Nervous", "NervousSystem100.glb", False),
    ("lymphoid", "Lymphoid", "LymphoidOrgans100.glb", False),
    ("joints", "Joints", "Joints100.glb", False),
    ("regions", "Regions", "Regions of human body100.glb", False),
]

# whole subtrees removed, by the exact name of their root node (see the module docstring)
EXCLUDE_SUBTREES: dict[str, dict[str, str]] = {
    "nervous": {
        "Telencephalon.g": "Brainder / White matter (licence not stated)",
        "White matter of spinal cord.g": "White matter (licence not stated), user decision",
        "White matter of diencephalon.g": "White matter (licence not stated), user decision",
        "Internal ear.g": "Anatomy of the Inner Ear (CC-BY-NC-SA)",
        "Tympanic membrane.l": "Anatomy of the Inner Ear (CC-BY-NC-SA), conservative",
        "Tympanic membrane.r": "Anatomy of the Inner Ear (CC-BY-NC-SA), conservative",
    },
    "skeletal": {
        "Auditory ossicles.g": "Anatomy of the Inner Ear (CC-BY-NC-SA), conservative",
    },
    "visceral": {
        "Kidney.l": "Kidney (CC-BY-NC)",
        "Kidney.r": "Kidney (CC-BY-NC)",
        "Renal pelvis.l": "Kidney (CC-BY-NC)",
        "Renal pelvis.r": "Kidney (CC-BY-NC)",
    },
    "cardiovascular": {
        "Intrarenal arteries of left kidney": "Kidney (CC-BY-NC)",
        "Intrarenal arteries of right kidney": "Kidney (CC-BY-NC)",
        "Intrarenal veins of left kidney": "Kidney (CC-BY-NC)",
        "Intrarenal veins of right kidney": "Kidney (CC-BY-NC)",
        "Anterior branch of renal artery.l": "Kidney (CC-BY-NC), conservative",
        "Anterior branch of renal artery.r": "Kidney (CC-BY-NC), conservative",
        "Posterior branch of renal artery.l": "Kidney (CC-BY-NC), conservative",
        "Posterior branch of renal artery.r": "Kidney (CC-BY-NC), conservative",
    },
}

_RESERVED = re.compile(r"[\[\]\.:/]")


def three_name(name: str) -> str:
    """The name three.js's GLTFLoader gives a node (PropertyBinding.sanitizeNodeName): the viewer
    keys selection on it, so the manifest must use exactly this."""
    return _RESERVED.sub("", re.sub(r"\s", "_", name))


def display_label(name: str) -> str:
    """'Hip bone.r' -> 'Hip bone (right)'; Z-Anatomy's .g/.j/.t/.i/.s markers are dropped."""
    m = re.match(r"^(.*)\.(l|r|g|j|t|i|s)$", name)
    if not m:
        return name.strip()
    stem, tag = m.group(1).strip(), m.group(2)
    return f"{stem} ({'left' if tag == 'l' else 'right'})" if tag in "lr" else stem


def description_key(name: str) -> str:
    """The Z-Anatomy description a node matches: its name without side/marker suffix."""
    return re.sub(r"\.(l|r|g|j|t|i|s)$", "", name).strip().lower()


# ---- GLB read / write ---------------------------------------------------------------------------


def read_glb(path: Path) -> tuple[dict[str, Any], bytes]:
    data = path.read_bytes()
    magic, version, _total = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67 or version != 2:
        raise SystemExit(f"{path.name}: not a glTF 2.0 binary")
    jlen, jtype = struct.unpack_from("<II", data, 12)
    gltf = json.loads(data[20 : 20 + jlen])
    off = 20 + jlen
    blen, btype = struct.unpack_from("<II", data, off)
    if jtype != 0x4E4F534A or btype != 0x004E4942:
        raise SystemExit(f"{path.name}: unexpected chunk layout")
    return gltf, data[off + 8 : off + 8 + blen]


def write_glb(gltf: dict[str, Any], binary: bytes) -> bytes:
    js = json.dumps(gltf, separators=(",", ":"), ensure_ascii=False).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(js), 0x4E4F534A)
        + js
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


# ---- pruning ------------------------------------------------------------------------------------


def prune(
    gltf: dict[str, Any], binary: bytes, roots: dict[str, str]
) -> tuple[dict[str, Any], bytes, list[dict[str, Any]]]:
    """Removes every node under a named root; drops meshes/accessors/bufferViews that only the
    removed nodes used. Returns (gltf, binary, removed rows)."""
    nodes = gltf["nodes"]
    children = {i: n.get("children", []) for i, n in enumerate(nodes)}
    parent = {c: i for i, cs in children.items() for c in cs}
    by_name: dict[str, list[int]] = {}
    for i, n in enumerate(nodes):
        by_name.setdefault(n.get("name", ""), []).append(i)
    missing = [r for r in roots if r not in by_name]
    if missing:
        raise SystemExit(f"exclusion roots not found (the source changed?): {missing}")

    removed: set[int] = set()
    rows: list[dict[str, Any]] = []
    for root_name, reason in roots.items():
        for root in by_name[root_name]:
            stack = [root]
            while stack:
                i = stack.pop()
                if i in removed:
                    continue
                removed.add(i)
                stack.extend(children[i])
                n = nodes[i]
                faces = 0
                if "mesh" in n:
                    for prim in gltf["meshes"][n["mesh"]]["primitives"]:
                        acc = prim.get("indices")
                        faces += gltf["accessors"][acc]["count"] // 3 if acc is not None else 0
                rows.append(
                    {"root": root_name, "node": n.get("name", ""), "faces": faces, "reason": reason}
                )

    keep = [i for i in range(len(nodes)) if i not in removed]
    node_map = {old: new for new, old in enumerate(keep)}
    used_meshes = sorted({nodes[i]["mesh"] for i in keep if "mesh" in nodes[i]})
    mesh_map = {old: new for new, old in enumerate(used_meshes)}

    used_acc: set[int] = set()
    for m in used_meshes:
        for prim in gltf["meshes"][m]["primitives"]:
            used_acc.update(prim.get("attributes", {}).values())
            if "indices" in prim:
                used_acc.add(prim["indices"])
            for target in prim.get("targets", []):
                used_acc.update(target.values())
    acc_keep = sorted(used_acc)
    acc_map = {old: new for new, old in enumerate(acc_keep)}
    bv_keep = sorted(
        {
            gltf["accessors"][a]["bufferView"]
            for a in acc_keep
            if "bufferView" in gltf["accessors"][a]
        }
    )
    if gltf.get("images"):
        raise SystemExit("images present: this builder does not carry image buffer views")

    # new binary: surviving buffer views in their original order, each 4-byte aligned
    chunks: list[bytes] = []
    bv_map: dict[int, int] = {}
    new_bvs = []
    offset = 0
    for new, old in enumerate(bv_keep):
        bv = dict(gltf["bufferViews"][old])
        start = bv.get("byteOffset", 0)
        blob = binary[start : start + bv["byteLength"]]
        pad = -offset % 4
        if pad:
            chunks.append(b"\0" * pad)
            offset += pad
        bv["byteOffset"] = offset
        chunks.append(blob)
        offset += len(blob)
        new_bvs.append(bv)
        bv_map[old] = new
    new_binary = b"".join(chunks)

    out = dict(gltf)
    new_nodes = []
    for i in keep:
        n = dict(nodes[i])
        if "children" in n:
            kids = [node_map[c] for c in n["children"] if c in node_map]
            if kids:
                n["children"] = kids
            else:
                n.pop("children")
        if "mesh" in n:
            n["mesh"] = mesh_map[n["mesh"]]
        new_nodes.append(n)
    out["nodes"] = new_nodes
    out["scenes"] = [
        {**s, "nodes": [node_map[i] for i in s.get("nodes", []) if i in node_map]}
        for s in gltf["scenes"]
    ]
    new_meshes = []
    for m in used_meshes:
        mesh = json.loads(json.dumps(gltf["meshes"][m]))
        for prim in mesh["primitives"]:
            prim["attributes"] = {k: acc_map[v] for k, v in prim["attributes"].items()}
            if "indices" in prim:
                prim["indices"] = acc_map[prim["indices"]]
            if "targets" in prim:
                prim["targets"] = [{k: acc_map[v] for k, v in t.items()} for t in prim["targets"]]
        new_meshes.append(mesh)
    out["meshes"] = new_meshes
    new_accs = []
    for a in acc_keep:
        acc = dict(gltf["accessors"][a])
        if "bufferView" in acc:
            acc["bufferView"] = bv_map[acc["bufferView"]]
        new_accs.append(acc)
    out["accessors"] = new_accs
    out["bufferViews"] = new_bvs
    out["buffers"] = [{"byteLength": len(new_binary) + (-len(new_binary) % 4)}]
    del parent
    return out, new_binary, rows


# ---- manifest -----------------------------------------------------------------------------------


def structures(gltf: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = gltf["nodes"]
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    seen: set[str] = set()
    rows = []
    for i, n in enumerate(nodes):
        if "mesh" not in n:
            continue
        name = n.get("name", "")
        node = three_name(name)
        if not node or node in seen:
            continue
        seen.add(node)
        faces = 0
        for prim in gltf["meshes"][n["mesh"]]["primitives"]:
            acc = prim.get("indices")
            faces += gltf["accessors"][acc]["count"] // 3 if acc is not None else 0
        chain, cur = [], i
        while cur in parent:
            cur = parent[cur]
            pname = nodes[cur].get("name", "")
            if pname.endswith(".g"):
                chain.append(display_label(pname))
        rows.append(
            {
                "node": node,
                "name": name,
                "label": display_label(name),
                "faces": faces,
                "path": list(reversed(chain)),
                "describe": description_key(name),
                "landmark": bool(re.search(r"\.(j|t|i|s)$", name)),
            }
        )
    rows.sort(key=lambda r: r["node"])
    return rows


def read_descriptions(folder: Path, wanted: set[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(folder.glob("*.txt")):
        key = path.stem.strip("() ").lower()
        if key not in wanted or key in out:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        paras: list[str] = []
        for block in re.split(r"\n\s*\n", text):
            block = " ".join(block.split())
            if not block or block.startswith("==") or block.upper() == block:
                if paras and block.startswith("=="):
                    break  # the intro ends at the first section heading
                continue
            paras.append(block.replace("( ", "(").replace(" )", ")").replace("(, ", "("))
            if sum(len(p) for p in paras) > 900:
                break
        if paras:
            out[key] = "\n\n".join(paras)
    return out


def main() -> int:
    holo = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_HOLOSCAN
    models = holo / "public" / "models"
    desc_dir = holo / "refs" / "3D" / "PC" / "Resources" / "Descriptions" / "OriginalDescriptions"
    licence_dir = holo / "refs" / "3D" / "PC"
    OUT.mkdir(parents=True, exist_ok=True)

    groups: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    sources = {}
    for gid, label, file, default_on in SYSTEMS:
        src = models / file
        gltf, binary = read_glb(src)
        sources[gid] = {"file": file, "sha256": hashlib.sha256(src.read_bytes()).hexdigest()}
        rows: list[dict[str, Any]] = []
        if gid in EXCLUDE_SUBTREES:  # noqa: SIM102 -- keeps the licence rule visible
            gltf, binary, rows = prune(gltf, binary, EXCLUDE_SUBTREES[gid])
        for r in rows:
            excluded.append({"group": gid, **r})
        # a system with nothing to remove is copied as it is, byte for byte
        blob = write_glb(gltf, binary) if rows else src.read_bytes()
        (OUT / f"{gid}.glb").write_bytes(blob)
        groups.append(
            {
                "id": gid,
                "label": label,
                "file": f"{gid}.glb",
                "bytes": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "default_on": default_on,
                "structures": structures(gltf),
            }
        )
        print(
            f"{gid:15} {len(blob) / 1e6:6.1f} MB  {len(groups[-1]['structures'])} structures, "
            f"{len(rows)} nodes removed"
        )

    wanted = {s["describe"] for g in groups for s in g["structures"]}
    descriptions = read_descriptions(desc_dir, wanted) if desc_dir.is_dir() else {}
    manifest = {
        "contract_version": "1.0",
        "kind": "atlas",
        "series_id": "atlas",
        "pipeline_version": "atlas",
        "segmentation": {
            "model": "Z-Anatomy atlas",
            "model_version": "Holoscan build",
            "label_set_version": "z-anatomy",
            "group_map_version": "holoscan-8",
        },
        "geometry": {
            "export_frame": "glTF_Y_up_m",
            "units": "metres",
            "shared_origin": True,
            "centring_offset_mm": [0.0, 0.0, 0.0],
        },
        "deface": {"required": False, "applied": False, "method": "not_a_patient"},
        "groups": groups,
        "not_meshed": [],
        "study": {
            "modality": None,
            "body_part": "WHOLE BODY",
            "slice_count": None,
            "voxel_spacing_mm": [],
            "shifted_study_date": None,
        },
        "licence": "CC-BY-SA 4.0 (Z-Anatomy, BodyParts3D CC-BY-SA 2.1 JP); see CREDITS.txt",
        "sources": sources,
        "excluded_nodes": len(excluded),
    }
    (OUT / "atlas.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", "utf-8")
    (OUT / "descriptions.json").write_text(
        json.dumps(descriptions, indent=0, sort_keys=True, ensure_ascii=False) + "\n", "utf-8"
    )

    lines = [
        "# Atlas parts NOT shipped",
        "",
        "Removed by `scripts/build_atlas.py` because their licence is non-commercial or not stated",
        "(see `LICENSES.md`). Everything else in the Holoscan atlas ships at original detail.",
        "",
        "| System | Removed subtree | Reason | Nodes | Faces |",
        "|---|---|---|---|---|",
    ]
    by_root: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in excluded:
        by_root.setdefault((r["group"], r["root"], r["reason"]), []).append(r)
    for (g, root, reason), rs in sorted(by_root.items()):
        lines.append(f"| {g} | `{root}` | {reason} | {len(rs)} | {sum(x['faces'] for x in rs):,} |")
    lines += ["", "## Every removed node", ""]
    for (g, root, _), rs in sorted(by_root.items()):
        names = sorted({x["node"] for x in rs if x["faces"]})
        lines.append(f"**{g} / {root}** ({len(names)} meshes): " + ", ".join(names))
        lines.append("")
    (OUT / "EXCLUDED.md").write_text("\n".join(lines) + "\n", "utf-8")

    for name, target in (
        ("LICENSE", "LICENSE-Z-Anatomy.txt"),
        ("README.md", "README-Z-Anatomy.md"),
    ):
        if (licence_dir / name).is_file():
            shutil.copyfile(licence_dir / name, OUT / target)
    lic = licence_dir / "Resources" / "Models" / "License.txt"
    if lic.is_file():
        shutil.copyfile(lic, OUT / "License-models.txt")
    (OUT / "CREDITS.txt").write_text(CREDITS, "utf-8")
    total = sum(g["bytes"] for g in groups)
    print(
        f"total {total / 1e6:.1f} MB, {len(excluded)} nodes removed, "
        f"{len(descriptions)} descriptions"
    )
    return 0


CREDITS = """\
Vesalius full-body atlas: credits and licence

The 3D anatomy is the Z-Anatomy atlas, as bundled with the Holoscan viewer:
  "BodyParts3D - The Database Center for Life Science - CC-BY-SA 2.1 Japan"
  "Z-Anatomy - The open source atlas of anatomy - CC-BY-SA 4.0"
  "Cranial Nerves and Foramina - by University of Dundee, CAHID - CC-BY 4.0"
Structure descriptions: Z-Anatomy's description texts (derived from Wikipedia, CC-BY-SA).

Changes: parts under non-commercial or unstated licences were removed (see EXCLUDED.md); nothing
else was modified. These files are distributed under CC-BY-SA 4.0 (share-alike).
Built by scripts/build_atlas.py; source file hashes are in atlas.json.
"""


if __name__ == "__main__":
    sys.exit(main())
