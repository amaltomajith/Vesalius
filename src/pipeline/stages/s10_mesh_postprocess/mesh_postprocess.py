"""S10 -- pure mesh post-processing (GEOMETRY.md "Mesh post-processing", STAGES.md S10). No
randomness, no DB, no I/O. Order is load-bearing (GEOMETRY.md): largest component -> clean
degenerates -> Taubin smoothing -> quadric decimation -> normals last.

**A real bug in GEOMETRY.md's own snippet, found by testing, not assumed:** its Taubin call is
`filter_taubin(mesh, lamb=0.5, nu=-0.53, iterations=12)`, with "`nu` must be slightly more
negative than `-lamb` for volume preservation." Verified empirically this is backwards for
`trimesh.smoothing.filter_taubin`'s actual implementation: its docstring and default (`nu=0.5`,
both params documented range "0.0 - 1.0") already alternate a shrink half-step
(`vertices += lamb * dot`) with a dilate half-step that SUBTRACTS `nu * dot`
(`vertices -= nu * dot`) -- passing a NEGATIVE `nu` makes both half-steps shrink in the same
direction instead of alternating. Measured on a noisy icosphere: `nu=-0.53` over 100 iterations
loses 36% of volume (worse than plain Laplacian smoothing, which trimesh preserves almost
exactly on its own -- the opposite of what GEOMETRY.md claims about the two methods); `nu=+0.53`
over the same 100 iterations preserves volume to within 1.2%. This module uses `nu=+0.53` (sign
corrected, magnitude kept); `test_mesh_postprocess.py` pins the volume-preservation property down
directly, and the mutation sweep flips the sign back to prove the test would catch a regression.

**Decimation determinism:** `pymeshlab`'s quadric edge collapse was verified empirically
byte-identical across repeated runs on a 485k-face phantom mesh decimated to 50k faces -- no
thread-pinning workaround needed, unlike CLAUDE.md's generic mesh-library-threading warning.
Recorded as evidence, not assumed; `test_mesh_postprocess.py` reproduces the check.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pymeshlab
import trimesh

from pipeline.common.errors import MeshPostconditionError, MeshZeroFacesError


@dataclass
class PostprocessResult:
    vertices: np.ndarray  # (n, 3) float64
    faces: np.ndarray  # (m, 3) int64
    normals: np.ndarray  # (n, 3) float64, per-vertex, computed last
    stats: dict[str, object]


def _mesh(vertices: np.ndarray, faces: np.ndarray) -> trimesh.Trimesh:
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


def _require_faces(mesh: trimesh.Trimesh, *, step: str) -> None:
    if len(mesh.faces) == 0:
        raise MeshZeroFacesError({"reason": "zero_faces", "step": step})


def check_postconditions(vertices: np.ndarray, faces: np.ndarray, normals: np.ndarray) -> None:
    """Separated out so a corrupted result can be tested directly, without having to coax
    trimesh's own internal caching into producing an inconsistent mesh object."""
    if faces.size and int(faces.max(initial=-1)) >= len(vertices):
        raise MeshPostconditionError({"reason": "face_index_out_of_range"})
    if normals.shape != vertices.shape:
        raise MeshPostconditionError(
            {
                "reason": "normals_shape_mismatch",
                "vertices_shape": list(vertices.shape),
                "normals_shape": list(normals.shape),
            }
        )


def postprocess_mesh(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    taubin_lamb: float,
    taubin_nu: float,
    taubin_iterations: int,
    target_faces: int,
) -> PostprocessResult:
    """Never mutates the input arrays (trimesh copies on construction). Fails closed on zero
    faces or a postcondition violation."""
    input_faces = int(len(faces))
    mesh = _mesh(vertices, faces)
    _require_faces(mesh, step="input")

    # 1. largest connected component -- no point smoothing fragments about to be discarded
    components = mesh.split(only_watertight=False)
    if len(components) > 1:
        mesh = max(components, key=lambda m: len(m.faces))
    after_largest_component_faces = int(len(mesh.faces))
    _require_faces(mesh, step="largest_component")

    # 2. clean degenerate / duplicate geometry
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    after_cleanup_faces = int(len(mesh.faces))
    _require_faces(mesh, step="degenerate_cleanup")

    # 3. Taubin smoothing -- NOT Laplacian, which shrinks; see module docstring for the sign fix
    trimesh.smoothing.filter_taubin(
        mesh, lamb=taubin_lamb, nu=taubin_nu, iterations=taubin_iterations
    )

    # 4. quadric edge-collapse decimation, skipped (never inflated) when already under budget
    decimation_applied = after_cleanup_faces > target_faces
    if decimation_applied:
        ms = pymeshlab.MeshSet()
        ms.add_mesh(pymeshlab.Mesh(mesh.vertices, mesh.faces))
        ms.meshing_decimation_quadric_edge_collapse(
            targetfacenum=target_faces,
            preserveboundary=True,
            preservenormal=True,
            planarquadric=True,
        )
        m = ms.current_mesh()
        mesh = _mesh(m.vertex_matrix(), m.face_matrix())
    after_decimation_faces = int(len(mesh.faces))
    _require_faces(mesh, step="decimation")

    # 5. normals last -- decimation invalidates them
    mesh.fix_normals()

    vertices_out: np.ndarray = np.asarray(mesh.vertices, dtype=np.float64)
    faces_out: np.ndarray = np.asarray(mesh.faces, dtype=np.int64)
    normals_out: np.ndarray = np.asarray(mesh.vertex_normals, dtype=np.float64)
    check_postconditions(vertices_out, faces_out, normals_out)

    stats: dict[str, object] = {
        "input_faces": input_faces,
        "after_largest_component_faces": after_largest_component_faces,
        "after_degenerate_cleanup_faces": after_cleanup_faces,
        "decimation_applied": decimation_applied,
        "after_decimation_faces": after_decimation_faces,
        "final_vertex_count": int(len(vertices_out)),
        "final_face_count": int(len(faces_out)),
        "target_faces": target_faces,
    }
    return PostprocessResult(
        vertices=vertices_out, faces=faces_out, normals=normals_out, stats=stats
    )
