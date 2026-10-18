"""Builds the landing page's anatomy statue (src/api/static/body/): solid, decimated meshes of four
body systems, aligned to the real head CT the page shows (src/api/static/scan/).

    python scripts/build_landing_body.py [ATLAS_DIR]

ATLAS_DIR defaults to assets/atlas (the licence-cleaned Holoscan atlas, built by
scripts/build_atlas.py; see assets/atlas/EXCLUDED.md). Needs trimesh + pymeshlab, so run it in the
api image rather than installing them on the host:

    docker run --rm -v "<repo>:/work" -w /work depipelining-api python scripts/build_landing_body.py

Source: Z-Anatomy, derived from BodyParts3D.
    "BodyParts3D - The Database Center for Life Science - CC-BY-SA 2.1 Japan"
    "Z-Anatomy - The open source atlas of anatomy - CC-BY-SA 4.0"
The shipped meshes are a derived work under CC-BY-SA 4.0 (share-alike); see body/CREDITS.txt.

Defence in depth on licences: assets/atlas already has the non-commercial / unstated-licence
parts removed, and every kidney, renal and ear node is dropped again here by name. The nervous
system is not used at all (its intracranial parts carried unstated licences). The visceral
system ships only a short list of named organs (no pleura, omentum or mesentery wrapping them).
Z-Anatomy's attachment patches and landmark meshes (suffixes .o*/.e*/.j/.i/.t/.g) are skipped.

Alignment: as scripts/build_landing_atlas.py did for the old point cloud, the atlas is turned half
a turn about Y (anterior -> -Z, the CT's frame), uniformly scaled and moved so its cranium matches
the real CT's bone. Never a mirror. Deterministic: same input, same bytes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# pymeshlab's decimation is not reproducible across threads; pin it before it loads
os.environ["OMP_NUM_THREADS"] = "1"

import numpy as np  # noqa: E402
import pymeshlab  # noqa: E402
import trimesh  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "src" / "api" / "static" / "scan"
OUT_DIR = ROOT / "src" / "api" / "static" / "body"
DEFAULT_ATLAS = ROOT / "assets" / "atlas"

# (name, file, triangle budget after decimation). Budgets keep every system under 65,536
# vertices, so indices ship as uint16.
SYSTEMS = [
    ("skeleton", "skeletal.glb", 100_000),
    ("muscle", "muscular.glb", 100_000),
    ("vessels", "cardiovascular.glb", 40_000),
    ("organs", "visceral.glb", 36_000),
]
EXCLUDE_ANY = re.compile(
    r"(?i)kidney|renal|\bear\b|ear\.|auricle|auricul(?!ar (?:surface|facet))|cochlea|vestibul|"
    r"semicircular|tympan|ossicle|incus|malleus|stapes|labyrinth|acoustic meatus"
)
SKIP_SUFFIX = re.compile(r"\.(o\d*[lr]?|e\d*[lr]?|j|i|t|g)$")
ORGANS_KEEP = re.compile(
    r"(?i)^(liver|stomach|pancreas|spleen|jejunum|ileum|duodenum|caecum|cecum|rectum|oesophagus|"
    r"trachea|urinary bladder|(ascending|transverse|descending|sigmoid) colon|"
    r"(superior|middle|inferior) lobe of (right|left) lung)$"
)
ATLAS_TO_HEAD = np.diag([-1.0, 1.0, -1.0])  # half a turn about Y: anterior -> -Z, as the CT
BAND = (0.03, 0.10)  # cranial breadth is measured this far below the vertex (metres)

# what the page labels in its "AI names every structure" beat: (label, system, node pattern)
ANCHORS = [
    ("Skull", "skeleton", r"^Parietal bone\.r$"),
    ("Humerus", "skeleton", r"^Humerus\.r$"),
    ("Pelvis", "skeleton", r"^Hip bone\.l$"),
    ("Femur", "skeleton", r"^Femur\.l$"),
    ("Tibia", "skeleton", r"^Tibia\.r$"),
    ("Heart", "vessels", r"^(Right|Left) (atrium|ventricle)$"),
    ("Aorta", "vessels", r"^Thoracic aorta$"),
    ("Lungs", "organs", r"^(superior|middle|inferior) lobe of right lung$"),
    ("Liver", "organs", r"^Liver$"),
    ("Stomach", "organs", r"^Stomach$"),
    ("Colon", "organs", r"^Transverse colon$"),
]


def base_name(node: str) -> str:
    return re.sub(r"_[0-9a-f]{6}$", "", node)


def load_scene(path: Path) -> trimesh.Scene:
    scene = trimesh.load(path, force="scene")
    assert isinstance(scene, trimesh.Scene)
    return scene


def keep(system: str, name: str) -> str | None:
    """Why a node is skipped, or None to keep it."""
    if EXCLUDE_ANY.search(name):
        return "licence"
    if SKIP_SUFFIX.search(name):
        return "overlay"
    if system == "organs" and not ORGANS_KEEP.match(name):
        return "not_an_organ"
    return None


def load_system(
    system: str, path: Path, body: np.ndarray | None
) -> tuple[trimesh.Trimesh, dict[str, int], dict[str, np.ndarray]]:
    """The kept nodes merged into one mesh (atlas frame), skip counts, and each kept node's
    vertices by name (for label anchors)."""
    scene = load_scene(path)
    parts: list[trimesh.Trimesh] = []
    skipped = {"licence": 0, "overlay": 0, "not_an_organ": 0, "outside_body": 0}
    by_name: dict[str, list[np.ndarray]] = {}

    def order(node: str) -> tuple[str, str]:
        # trimesh suffixes duplicate node names with RANDOM hex, so the name alone does not give
        # a stable order: tie-break on the node's content
        transform, geom = scene.graph[node]
        digest = hashlib.sha256(np.asarray(transform).tobytes())
        digest.update(np.asarray(scene.geometry[geom].vertices).tobytes())
        return base_name(node), digest.hexdigest()

    for node in sorted(scene.graph.nodes_geometry, key=order):
        name = base_name(node)
        reason = keep(system, name)
        if reason:
            skipped[reason] += 1
            continue
        transform, geom = scene.graph[node]
        mesh = scene.geometry[geom].copy()
        mesh.apply_transform(transform)
        if body is not None:
            lo, hi = body
            pad = 0.05 * (hi - lo)
            if (mesh.bounds[0] < lo - pad).any() or (mesh.bounds[1] > hi + pad).any():
                skipped["outside_body"] += 1
                continue
        parts.append(trimesh.Trimesh(mesh.vertices, mesh.faces, process=False))
        by_name.setdefault(name, []).append(np.asarray(mesh.vertices))
    merged = trimesh.util.concatenate(parts)
    assert isinstance(merged, trimesh.Trimesh)
    merged.merge_vertices()
    return merged, skipped, {k: np.vstack(v) for k, v in by_name.items()}


def decimate(mesh: trimesh.Trimesh, faces: int) -> trimesh.Trimesh:
    ms = pymeshlab.MeshSet()
    ms.add_mesh(pymeshlab.Mesh(vertex_matrix=mesh.vertices, face_matrix=mesh.faces))
    ms.meshing_decimation_quadric_edge_collapse(
        targetfacenum=faces,
        qualitythr=0.4,
        preservenormal=True,
        optimalplacement=True,
        planarquadric=True,
        autoclean=True,
    )
    out = ms.current_mesh()
    mesh = trimesh.Trimesh(out.vertex_matrix(), out.face_matrix(), process=False)
    # collapsed slivers (thin vessels) leave zero-area faces, whose vertices get no normal and
    # would render black: drop them and anything they alone referenced
    mesh.update_faces(mesh.nondegenerate_faces(height=1e-9))
    mesh.remove_unreferenced_vertices()
    return mesh


def read_ct_bone() -> np.ndarray:
    meta = json.loads((SCAN / "head_points.json").read_text(encoding="utf-8"))
    buf = (SCAN / "head_points.bin").read_bytes()
    n = meta["count"]
    q: np.ndarray = np.frombuffer(buf, "<i2", n * 3).reshape(n, 3)
    tissue: np.ndarray = np.frombuffer(buf, "u1", n, offset=n * 9)
    return q[tissue == 1].astype(float) * meta["extent_m"] / 32767


def cranium(points: np.ndarray) -> tuple[float, float, float, float]:
    """(vertex y, breadth, x centre, z centre) of the band BAND below the vertex."""
    top = float(points[:, 1].max())
    band = points[(points[:, 1] < top - BAND[0]) & (points[:, 1] > top - BAND[1])]
    lo, hi = np.percentile(band[:, 0], [0.5, 99.5])
    zlo, zhi = np.percentile(band[:, 2], [0.5, 99.5])
    return top, float(hi - lo), float((hi + lo) / 2), float((zhi + zlo) / 2)


def main() -> int:
    atlas = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ATLAS
    names = [s[0] for s in SYSTEMS]

    skeleton_scene = load_scene(atlas / SYSTEMS[0][1])
    c1 = [
        trimesh.transform_points(skeleton_scene.geometry[g].vertices, t)[:, 1].max()
        for t, g in (
            skeleton_scene.graph[n]
            for n in skeleton_scene.graph.nodes_geometry
            if base_name(n) == "Atlas (C1)"
        )
    ]
    assert c1, "the atlas vertebra (C1) separates the cranium from the body; not found"
    c1_y = float(max(c1))

    meshes: list[trimesh.Trimesh] = []
    sources: dict[str, Any] = {}
    nodes: dict[str, dict[str, np.ndarray]] = {}
    body = None
    for name, file, budget in SYSTEMS:
        path = atlas / file
        mesh, skipped, by_name = load_system(name, path, body)
        if body is None:
            body = mesh.bounds
        full = len(mesh.faces)
        mesh = decimate(mesh, budget)
        meshes.append(mesh)
        nodes[name] = by_name
        sources[name] = {
            "file": file,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "nodes": len(by_name),
            "faces_source": full,
            "skipped": skipped,
        }

    # one frame for everything: half a turn about Y, then the cranium onto the CT's
    skull = meshes[0].vertices @ ATLAS_TO_HEAD.T
    skull = skull[skull[:, 1] > c1_y + 0.02]
    a_top, a_breadth, a_x, a_z = cranium(skull)
    c_top, c_breadth, c_x, c_z = cranium(read_ct_bone())
    scale = c_breadth / a_breadth

    def to_head(p: np.ndarray) -> np.ndarray:
        return (p @ ATLAS_TO_HEAD.T - np.array([a_x, a_top, a_z])) * scale + np.array(
            [c_x, c_top, c_z]
        )

    def vertex_normals(m: trimesh.Trimesh) -> np.ndarray:
        n = np.array(m.vertex_normals)
        # back-to-back duplicate faces cancel to a zero normal: take one adjacent face's instead
        flat = np.linalg.norm(n, axis=1) < 0.5
        if flat.any():
            first = m.vertex_faces[flat][:, 0]
            n[flat] = m.face_normals[first]
        return n

    positions = [to_head(np.asarray(m.vertices)) for m in meshes]
    normals = [vertex_normals(m) @ ATLAS_TO_HEAD.T for m in meshes]
    extent = float(max(np.abs(p).max() for p in positions))

    pos_blob, nrm_blob, idx_blob = b"", b"", b""
    systems: list[dict[str, Any]] = []
    for name, mesh, pos, nrm in zip(names, meshes, positions, normals, strict=True):
        v = len(pos)
        assert v < 65_536, f"{name}: {v} vertices do not fit uint16 indices; lower its budget"
        q = np.rint(pos / extent * 32767).astype("<i2")
        length = np.linalg.norm(nrm, axis=1, keepdims=True)
        assert (length > 0.5).all(), f"{name}: {(length <= 0.5).sum()} vertices have no normal"
        unit = nrm / length
        qn = np.clip(np.rint(unit * 127), -127, 127).astype("i1")
        idx: np.ndarray = np.asarray(mesh.faces, dtype="<u2").ravel()
        systems.append(
            {
                "name": name,
                "vertices": v,
                "triangles": len(mesh.faces),
                "pos_offset": len(pos_blob),
                "nrm_offset": len(nrm_blob),
                "idx_offset": len(idx_blob),
            }
        )
        pos_blob += q.tobytes()
        nrm_blob += qn.tobytes()
        idx_blob += idx.tobytes()
    # sections: positions | normals (padded to 2 bytes) | indices
    nrm_start = len(pos_blob)
    idx_start = nrm_start + len(nrm_blob) + (len(nrm_blob) % 2)
    blob = pos_blob + nrm_blob + b"\0" * (len(nrm_blob) % 2) + idx_blob
    for s in systems:
        s["nrm_offset"] += nrm_start
        s["idx_offset"] += idx_start
        s.update(sources[s["name"]])

    anchors = []
    for label, system, pattern in ANCHORS:
        rx = re.compile(pattern, re.I)
        found = [v for n, v in nodes[system].items() if rx.match(n)]
        assert found, f"anchor {label}: no {system} node matches {pattern}"
        pts = to_head(np.vstack(found))
        centre = (pts.min(0) + pts.max(0)) / 2
        anchors.append({"label": label, "system": system, "at": centre.round(4).tolist()})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "body.bin").write_bytes(blob)
    all_pos = np.vstack(positions)
    meta = {
        "bytes": len(blob),
        "layout": (
            "per system, at its offsets: int16 xyz[vertices] (x extent_m / 32767) | int8 normal "
            "xyz[vertices] (/ 127) | uint16 index[triangles * 3]"
        ),
        "systems": systems,
        "extent_m": extent,
        "bounds_m": [all_pos.min(0).round(4).tolist(), all_pos.max(0).round(4).tolist()],
        "frame": "the head CT cloud's (scan/head_points.json): glTF metres, Y up, anterior -Z",
        "alignment": {
            "scale": round(scale, 5),
            "atlas_cranium": {"vertex_y": round(a_top, 6), "breadth": round(a_breadth, 6)},
            "ct_cranium": {"vertex_y": round(c_top, 6), "breadth": round(c_breadth, 6)},
            "band_below_vertex_m": list(BAND),
        },
        "anchors": anchors,
        "licence": "CC-BY-SA 4.0 (derived from Z-Anatomy / BodyParts3D); see CREDITS.txt",
        "excluded": (
            "nervous system; kidney/renal/ear nodes; visceral nodes other than named organs"
        ),
    }
    (OUT_DIR / "body.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", "utf-8")
    (OUT_DIR / "CREDITS.txt").write_text(CREDITS, "utf-8")
    print(json.dumps({k: meta[k] for k in ("bytes", "bounds_m", "alignment")}, indent=1))
    for s in systems:
        print(s["name"], s["vertices"], "verts", s["triangles"], "tris", s["skipped"])
    return 0


CREDITS = """\
Landing page anatomy statue: data credit and licence

Derived from the Z-Anatomy atlas as bundled with the Holoscan viewer:
  "BodyParts3D - The Database Center for Life Science - CC-BY-SA 2.1 Japan"
  "Z-Anatomy - The open source atlas of anatomy - CC-BY-SA 4.0"

Changes: the skeletal, muscular and cardiovascular systems and a short list of named organs were
merged per system, decimated, uniformly scaled and moved to line up with a real head CT.
Excluded: the nervous system and every kidney, renal and ear structure (third-party parts under
non-commercial or unstated licences). These meshes (body.bin / body.json) are licensed
CC-BY-SA 4.0. Built by scripts/build_landing_body.py; source file hashes are in body.json.
"""


if __name__ == "__main__":
    sys.exit(main())
