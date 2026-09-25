"""S10's mesh serialization: binary PLY via `trimesh`, same rationale and the same verified
float32-precision property as S9's `mesh_io.py` (not reused directly -- this one also carries
per-vertex normals, which S9's raw mesh deliberately withholds; see S10's module docstring for
why they belong here). `process=False` on both write and read for the same reason S9 uses it:
trimesh's default processing would silently change the vertex/face counts post-processing
actually produced.

Normals are stored as `float32` too (same PLY property type as vertices), so the write-path
check in `service.py` compares them with a tolerance, exactly like the vertex positions.
"""

from __future__ import annotations

import io

import numpy as np
import trimesh


def write_mesh(vertices: np.ndarray, faces: np.ndarray, normals: np.ndarray) -> bytes:
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=normals, process=False)
    buf = io.BytesIO()
    mesh.export(file_obj=buf, file_type="ply", vertex_normal=True)
    return buf.getvalue()


def load_mesh(ply_bytes: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mesh = trimesh.load(io.BytesIO(ply_bytes), file_type="ply", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"PLY did not load as a triangle mesh (got {type(mesh).__name__})")
    vertices: np.ndarray = np.asarray(mesh.vertices, dtype=np.float64)
    faces: np.ndarray = np.asarray(mesh.faces, dtype=np.int64)
    normals: np.ndarray = np.asarray(mesh.vertex_normals, dtype=np.float64)
    if normals.shape != vertices.shape:
        raise ValueError(
            f"PLY carried no usable vertex normals (shape {normals.shape} != {vertices.shape})"
        )
    return vertices, faces, normals
