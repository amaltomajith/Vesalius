"""S9's mesh serialization: binary PLY via `trimesh` -- a real, tool-inspectable mesh format
(same rationale as using NIfTI, not a bespoke array dump, for volumes/masks). `process=False` on
both write and read is deliberate: trimesh's default processing merges near-duplicate vertices
and drops degenerate faces, which would silently change the vertex/face counts marching_cubes
actually produced -- exactly the kind of silent alteration the write-path re-read check exists
to catch, so it must not happen on either side of the round trip.

**Verified empirically, not assumed:** trimesh's binary PLY writer stores vertex coordinates as
IEEE `float32` (`trimesh.exchange.ply.export_ply`'s `dtype_vertex = [("vertex", "<f4", (3))]`,
hardcoded, no float64 option). Face indices are `int32`. A round trip through this module is
therefore NOT bit-exact even on a correct write -- `write_mesh` then `load_mesh` legitimately
changes vertex values by up to float32 rounding (~1e-7 relative, at most a few 1e-4mm at CT
scale, negligible for a physical mesh). The write-path check in `service.py` compares with a
tolerance for exactly this reason; face indices, which are integers, are still compared exactly.
"""

from __future__ import annotations

import io

import numpy as np
import trimesh


def write_mesh(vertices_ras: np.ndarray, faces: np.ndarray) -> bytes:
    mesh = trimesh.Trimesh(vertices=vertices_ras, faces=faces, process=False)
    buf = io.BytesIO()
    mesh.export(file_obj=buf, file_type="ply")
    return buf.getvalue()


def load_mesh(ply_bytes: bytes) -> tuple[np.ndarray, np.ndarray]:
    mesh = trimesh.load(io.BytesIO(ply_bytes), file_type="ply", process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError(f"PLY did not load as a triangle mesh (got {type(mesh).__name__})")
    return np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)
