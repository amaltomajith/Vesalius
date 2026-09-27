"""Builds the landing page's full-body atlas point cloud (src/api/static/atlas/), aligned to the
real head CT that the page dives into (src/api/static/scan/, from build_landing_assets.py).

    python scripts/build_landing_atlas.py [MODELS_DIR]

MODELS_DIR defaults to F:/Projects/Holoscan/public/models.

Source: the Holoscan viewer's anatomy atlas, Z-Anatomy, derived from BodyParts3D.
    "BodyParts3D - The Database Center for Life Science - CC-BY-SA 2.1 Japan"
    "Z-Anatomy - The open source atlas of anatomy - CC-BY-SA 4.0"
The shipped point cloud is a derived work and is distributed under CC-BY-SA 4.0 (share-alike);
see atlas/CREDITS.txt, written next to it.

The atlas bundles a few third-party models under OTHER licences (Holoscan's
refs/3D/PC/Resources/Models/License.txt, recorded in LICENSES.md). Node names cannot prove which
geometry came from them, so exclusion is deliberately conservative:
  * the visceral system is not used at all (it holds the CC-BY-NC "Kidney");
  * any node naming the kidney or kidney vessels is dropped from every system;
  * any node naming the ear (inner ear is CC-BY-NC-SA; the ossicles go too) is dropped;
  * every nervous-system point inside the cranium is dropped: that is where "Brainder" and
    "White matter" (licence not stated) live. The page shows the real CT there instead.
Z-Anatomy's muscle-attachment patches and landmark meshes (suffixes .o*/.e*/.j/.i/.t/.g) overlap
the bone surface and are skipped; meshes whose extent is not inside the body (a broken transform
in the nervous file reports thousands of square metres) are skipped and counted.

Alignment: the atlas is scaled and moved so its cranium (vertex and cranial breadth) matches the
real CT's bone, in the CT cloud's own frame (glTF metres, anterior -Z, as S11 exports it). It is a
rotation + uniform scale + translation, never a mirror. Deterministic (fixed seeds).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
SCAN = ROOT / "src" / "api" / "static" / "scan"
OUT_DIR = ROOT / "src" / "api" / "static" / "atlas"
DEFAULT_MODELS = Path("F:/Projects/Holoscan/public/models")

SYSTEMS = [  # (id, file, points)
    ("skeleton", "SkeletalSystem100.glb", 180_000),
    ("vessels", "CardioVascular41.glb", 80_000),
    ("nerves", "NervousSystem100.glb", 40_000),
]
SEED = 20260927
EXCLUDE_ANY = re.compile(
    r"(?i)kidney|renal|\bear\b|ear\.|auricle|auricul(?!ar (?:surface|facet))|cochlea|vestibul|"
    r"semicircular|tympan|ossicle|incus|malleus|stapes|labyrinth|acoustic meatus"
)
SKIP_SUFFIX = re.compile(r"\.(o\d*[lr]?|e[lr]?|j|i|t|g)$")
# atlas frame: metres, Y up, anterior +Z (checked: sternum z +0.13 vs thoracic vertebrae -0.08)
ATLAS_TO_HEAD = np.diag([-1.0, 1.0, -1.0])  # half a turn about Y: anterior -> -Z, as the CT
BAND = (0.03, 0.10)  # cranial breadth is measured this far below the vertex (metres)


def base_name(node: str) -> str:
    return re.sub(r"_[0-9a-f]{6}$", "", node)


def load_scene(path: Path) -> trimesh.Scene:
    scene = trimesh.load(path, force="scene")
    assert isinstance(scene, trimesh.Scene)
    return scene


def load_system(path: Path, body: np.ndarray | None) -> tuple[trimesh.Trimesh, dict[str, int]]:
    scene = load_scene(path)
    parts: list[trimesh.Trimesh] = []
    skipped = {"licence": 0, "overlay": 0, "outside_body": 0}
    for node in scene.graph.nodes_geometry:
        name = base_name(node)
        if EXCLUDE_ANY.search(name):
            skipped["licence"] += 1
            continue
        if SKIP_SUFFIX.search(name):
            skipped["overlay"] += 1
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
        parts.append(mesh)
    merged = trimesh.util.concatenate(parts)
    assert isinstance(merged, trimesh.Trimesh)
    return merged, skipped


def sample(mesh: trimesh.Trimesh, n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    pts, face = trimesh.sample.sample_surface(mesh, n, seed=seed)
    return np.asarray(pts), np.asarray(mesh.face_normals[face])


def read_ct_bone() -> np.ndarray:
    meta = json.loads((SCAN / "head_points.json").read_text(encoding="utf-8"))
    buf = (SCAN / "head_points.bin").read_bytes()
    n = meta["count"]
    q = np.frombuffer(buf, "<i2", n * 3).reshape(n, 3)
    tissue = np.frombuffer(buf, "u1", n, offset=n * 9)
    return q[tissue == 1].astype(float) * meta["extent_m"] / 32767


def cranium(points: np.ndarray) -> tuple[float, float, float, float]:
    """(vertex y, breadth, x centre, z centre) of the band BAND below the vertex."""
    top = float(points[:, 1].max())
    band = points[(points[:, 1] < top - BAND[0]) & (points[:, 1] > top - BAND[1])]
    lo, hi = np.percentile(band[:, 0], [0.5, 99.5])
    zlo, zhi = np.percentile(band[:, 2], [0.5, 99.5])
    return top, float(hi - lo), float((hi + lo) / 2), float((zhi + zlo) / 2)


def main() -> int:
    models = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MODELS
    sources: dict[str, Any] = {}
    skeleton, sk_skipped = load_system(models / SYSTEMS[0][1], None)
    body = skeleton.bounds
    c1_y = None
    # the top of the cervical spine (atlas vertebra) separates the cranium from the body
    sc = load_scene(models / SYSTEMS[0][1])
    ys = [
        trimesh.transform_points(sc.geometry[g].vertices, t)[:, 1].max()
        for t, g in (
            sc.graph[n] for n in sc.graph.nodes_geometry if re.match(r"(?i)atlas\b", base_name(n))
        )
    ]
    c1_y = float(max(ys))

    pos_all: list[np.ndarray] = []
    nrm_all: list[np.ndarray] = []
    sys_all: list[np.ndarray] = []
    for sid, (name, file, count) in enumerate(SYSTEMS):
        path = models / file
        sources[name] = {"file": file, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        mesh, skipped = (skeleton, sk_skipped) if sid == 0 else load_system(path, body)
        # oversample, then drop what must not ship, then take exactly `count` deterministically
        pts, nrm = sample(mesh, int(count * 1.6), SEED + sid)
        if name == "nerves":
            keep = pts[:, 1] < c1_y  # nothing intracranial (see module docstring)
            skipped["intracranial_points"] = int((~keep).sum())
            pts, nrm = pts[keep], nrm[keep]
        idx = np.random.default_rng(SEED + 10 + sid).permutation(len(pts))[:count]
        pos_all.append(pts[idx])
        nrm_all.append(nrm[idx])
        sys_all.append(np.full(len(idx), sid, np.uint8))
        sources[name].update({"points": int(len(idx)), "skipped": skipped})

    pos = np.vstack(pos_all) @ ATLAS_TO_HEAD.T
    nrm = np.vstack(nrm_all) @ ATLAS_TO_HEAD.T
    system = np.concatenate(sys_all)

    # align the atlas cranium to the real CT's bone
    skull = pos[(system == 0) & (pos[:, 1] > c1_y + 0.02)]
    a_top, a_breadth, a_x, a_z = cranium(skull)
    c_top, c_breadth, c_x, c_z = cranium(read_ct_bone())
    scale = c_breadth / a_breadth
    pos = (pos - np.array([a_x, a_top, a_z])) * scale + np.array([c_x, c_top, c_z])

    extent = float(np.abs(pos).max())
    q = np.rint(pos / extent * 32767).astype("<i2")
    qn = np.clip(np.rint(nrm / np.linalg.norm(nrm, axis=1, keepdims=True) * 127), -127, 127).astype(
        "i1"
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blob = q.tobytes() + qn.tobytes() + system.tobytes()
    (OUT_DIR / "atlas_points.bin").write_bytes(blob)
    lo, hi = pos.min(0), pos.max(0)
    meta = {
        "count": int(len(pos)),
        "bytes": len(blob),
        "bytes_per_point": 10,
        "layout": (
            "int16 xyz[count] (x extent_m / 32767) | int8 normal xyz[count] (/ 127)"
            " | uint8 system[count]"
        ),
        "systems": [s[0] for s in SYSTEMS],
        "extent_m": extent,
        "bounds_m": [lo.round(4).tolist(), hi.round(4).tolist()],
        "frame": "the head CT cloud's (scan/head_points.json): glTF metres, Y up, anterior -Z",
        "alignment": {
            "scale": round(scale, 5),
            "atlas_cranium": {"vertex_y": a_top, "breadth": a_breadth},
            "ct_cranium": {"vertex_y": c_top, "breadth": c_breadth},
            "band_below_vertex_m": list(BAND),
            # no nervous-system point lies above this height (the intracranial cut, this frame)
            "nerves_below_y_m": (c1_y - a_top) * scale + c_top,
        },
        "licence": "CC-BY-SA 4.0 (derived from Z-Anatomy / BodyParts3D); see CREDITS.txt",
        "sources": sources,
        "excluded": "visceral system; kidney/ear nodes; all intracranial nervous points",
    }
    (OUT_DIR / "atlas_points.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", "utf-8"
    )
    (OUT_DIR / "CREDITS.txt").write_text(CREDITS, "utf-8")
    print(json.dumps({k: meta[k] for k in ("count", "bytes", "bounds_m", "alignment")}, indent=1))
    return 0


CREDITS = """\
Landing page full-body atlas: data credit and licence

Derived from the Z-Anatomy atlas as bundled with the Holoscan viewer:
  "BodyParts3D - The Database Center for Life Science - CC-BY-SA 2.1 Japan"
  "Z-Anatomy - The open source atlas of anatomy - CC-BY-SA 4.0"

Changes: surface points sampled from the skeletal, cardiovascular and nervous systems, uniformly
scaled and moved to line up with a real head CT. Excluded: the visceral system, every kidney and
ear structure, and every intracranial nervous structure (third-party parts under non-commercial
or unstated licences). This point cloud (atlas_points.bin / .json) is licensed CC-BY-SA 4.0.
Built by scripts/build_landing_atlas.py; source file hashes are in atlas_points.json.
"""


if __name__ == "__main__":
    sys.exit(main())
