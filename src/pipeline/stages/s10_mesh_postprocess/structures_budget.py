"""S10 structures mode -- per-group face budgets and per-structure clean-up (refs/INTEGRATION.md
§2, S10). Pure: no DB, no I/O.

"Budget is per group, allocated across that group's structures proportionally to their
pre-decimation face counts. Small structures keep a floor of ~800 faces so they stay
recognisable." A floor can push a group slightly over its budget; that is accepted and reported,
because a structure shrunk below recognisability is worse than 800 extra faces.

The verified single-mesh `postprocess_mesh` keeps only the LARGEST connected component. Many
anatomical labels are legitimately multi-part (`costal_cartilages`, `colon` loops, `small_bowel`),
so here each connected component of a structure is processed on its own, with its share of the
structure's budget, and the results are merged. Only specks below `MIN_COMPONENT_FACES` are left
out -- counted in the report, not silently lost.
"""

from __future__ import annotations

import numpy as np
import trimesh

from pipeline.stages.s10_mesh_postprocess import params
from pipeline.stages.s10_mesh_postprocess.mesh_postprocess import postprocess_mesh

BUDGET_VERSION = "v1"
FACE_BUDGET_V1: dict[str, int] = {
    "skeletal": 120_000,
    "cardiovascular": 60_000,
    "visceral": 80_000,
    "respiratory": 40_000,
    "muscular": 40_000,
    "nervous": 30_000,
    "endocrine": 10_000,
    "other": 20_000,
}
FLOOR_FACES = 800
MIN_COMPONENT_FACES = 60


def structures_algorithm_params() -> dict[str, object]:
    return {
        "mode": "structures",
        "budget_version": BUDGET_VERSION,
        "face_budget": dict(FACE_BUDGET_V1),
        "floor_faces": FLOOR_FACES,
        "min_component_faces": MIN_COMPONENT_FACES,
        "taubin_lamb": params.TAUBIN_LAMB,
        "taubin_nu": params.TAUBIN_NU,
        "taubin_iterations": params.TAUBIN_ITERATIONS,
        "decimator": params.DECIMATOR,
    }


def allocate(raw_faces: dict[str, int], group_of: dict[str, str]) -> dict[str, int]:
    """Per-structure target faces: the group's budget shared by raw face count, floored at
    FLOOR_FACES, and never above the structure's own raw count (decimation never inflates)."""
    by_group: dict[str, list[str]] = {}
    for node in raw_faces:
        by_group.setdefault(group_of[node], []).append(node)
    targets: dict[str, int] = {}
    for group, nodes in by_group.items():
        budget = FACE_BUDGET_V1.get(group, FACE_BUDGET_V1["other"])
        total = sum(raw_faces[n] for n in nodes) or 1
        for n in nodes:
            share = int(round(budget * raw_faces[n] / total))
            targets[n] = min(raw_faces[n], max(FLOOR_FACES, share))
    return targets


def process_structure(
    vertices: np.ndarray, faces: np.ndarray, target_faces: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    """Clean, smooth and decimate one structure component by component. Returns merged
    (vertices, faces, normals, stats)."""
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    parts = [p for p in mesh.split(only_watertight=False)]
    kept = [p for p in parts if len(p.faces) >= MIN_COMPONENT_FACES]
    dropped_faces = sum(len(p.faces) for p in parts if len(p.faces) < MIN_COMPONENT_FACES)
    if not kept:  # a structure made only of specks: keep its largest piece rather than nothing
        kept = [max(parts, key=lambda p: len(p.faces))]
        dropped_faces -= len(kept[0].faces)
    total = sum(len(p.faces) for p in kept)
    vs: list[np.ndarray] = []
    fs: list[np.ndarray] = []
    ns: list[np.ndarray] = []
    offset = 0
    for p in sorted(kept, key=lambda p: -len(p.faces)):
        share = max(4, int(round(target_faces * len(p.faces) / total)))
        r = postprocess_mesh(
            np.asarray(p.vertices),
            np.asarray(p.faces),
            taubin_lamb=params.TAUBIN_LAMB,
            taubin_nu=params.TAUBIN_NU,
            taubin_iterations=params.TAUBIN_ITERATIONS,
            target_faces=share,
        )
        vs.append(r.vertices)
        fs.append(r.faces + offset)
        ns.append(r.normals)
        offset += len(r.vertices)
    stats = {
        "components": len(parts),
        "components_kept": len(kept),
        "speck_faces_left_out": int(max(0, dropped_faces)),
    }
    return np.vstack(vs), np.vstack(fs), np.vstack(ns), stats
