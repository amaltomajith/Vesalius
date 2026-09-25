"""S11's GLB serialization. Same `process=False` discipline as S9/S10's PLY writers (trimesh's
default processing would silently change the vertex/face counts export produced), and the same
verified-empirically property: glTF's binary accessor encoding is float32, so a write -> re-read
round trip is close, not bit-exact, even when correct.

`trimesh.load(..., file_type="glb")` returns a `Scene`, not a bare `Trimesh` (unlike PLY) --
GEOMETRY.md's own "Validate what you wrote" snippet glosses over this. There is exactly one mesh
in the scene (this module never writes more than one), so the single geometry is extracted
directly; more than one would mean something upstream changed unexpectedly.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
import trimesh


@dataclass(frozen=True)
class GlbWriteResult:
    data: bytes
    face_count: int
    is_watertight: bool  # of the mesh actually written, for the write-path re-read check


def write_glb(vertices: np.ndarray, faces: np.ndarray, normals: np.ndarray) -> GlbWriteResult:
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=normals, process=False)
    buf = io.BytesIO()
    mesh.export(file_obj=buf, file_type="glb")
    return GlbWriteResult(
        data=buf.getvalue(), face_count=len(mesh.faces), is_watertight=bool(mesh.is_watertight)
    )


def load_glb(glb_bytes: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    """Returns (vertices, faces, normals, is_watertight)."""
    loaded = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", process=False)
    if isinstance(loaded, trimesh.Scene):
        geoms = list(loaded.geometry.values())
        if len(geoms) != 1:
            raise ValueError(f"GLB scene held {len(geoms)} geometries, expected exactly 1")
        mesh = geoms[0]
    else:
        mesh = loaded
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"GLB did not load as a triangle mesh (got {type(mesh).__name__})")
    vertices: np.ndarray = np.asarray(mesh.vertices, dtype=np.float64)
    faces: np.ndarray = np.asarray(mesh.faces, dtype=np.int64)
    normals: np.ndarray = np.asarray(mesh.vertex_normals, dtype=np.float64)
    if normals.shape != vertices.shape:
        raise ValueError(
            f"GLB carried no usable vertex normals (shape {normals.shape} != {vertices.shape})"
        )
    return vertices, faces, normals, bool(mesh.is_watertight)
