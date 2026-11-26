"""S11 structures mode -- one multi-node GLB per body-system group (refs/INTEGRATION.md §2, S11).

"Node naming is the contract. Node name is exactly the segmentation label, unmodified." and
"Apply the LPS->glTF conversion once at group level so every group shares an origin." Every group
of a series is therefore written with ONE centring offset (the bounding-box centre of all its
structures, in RAS mm) and S11's own axis/scale convention (RAS -> glTF Y-up metres).

`glb_node_names` reads the node names straight out of the GLB's JSON chunk -- deliberately NOT
through trimesh, the library that wrote them -- so the survival check is independent of the
writer (refs step 4: "Open a GLB in a glTF viewer and confirm node names survive export").
"""

from __future__ import annotations

import json
import struct

import numpy as np
import trimesh

from pipeline.stages.s11_export.gltf_transform import MM_TO_M, RAS_TO_GLTF_3X3


def shared_offset_mm(vertex_sets: list[np.ndarray]) -> np.ndarray:
    """Translation moving the bounding-box centre of ALL structures to the origin (RAS mm)."""
    allv = np.vstack(vertex_sets)
    return np.asarray(-(allv.min(axis=0) + allv.max(axis=0)) / 2.0)


def to_gltf(
    vertices_ras_mm: np.ndarray, normals_ras: np.ndarray, offset_mm: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    return (
        ((vertices_ras_mm + offset_mm) @ RAS_TO_GLTF_3X3.T) * MM_TO_M,
        normals_ras @ RAS_TO_GLTF_3X3.T,
    )


def build_group_glb(structures: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]]) -> bytes:
    """`structures`: (node, vertices glTF m, faces, normals glTF). One node + mesh per structure,
    both named exactly `node`. Sorted by node so the bytes are deterministic."""
    scene = trimesh.Scene()
    for node, v, f, n in sorted(structures, key=lambda s: s[0]):
        mesh = trimesh.Trimesh(vertices=v, faces=f, vertex_normals=n, process=False)
        scene.add_geometry(mesh, node_name=node, geom_name=node)
    return bytes(scene.export(file_type="glb"))


def glb_json(glb: bytes) -> dict[str, object]:
    magic, _version, _length = struct.unpack_from("<4sII", glb, 0)
    if magic != b"glTF":
        raise ValueError("not a GLB")
    chunk_len, chunk_type = struct.unpack_from("<II", glb, 12)
    if chunk_type != 0x4E4F534A:  # "JSON"
        raise ValueError("first GLB chunk is not JSON")
    return dict(json.loads(glb[20 : 20 + chunk_len]))


def glb_node_names(glb: bytes) -> list[str]:
    nodes = glb_json(glb).get("nodes", [])
    if not isinstance(nodes, list):
        raise ValueError("GLB JSON has no node list")
    return sorted(str(n.get("name")) for n in nodes if isinstance(n, dict) and "mesh" in n)
