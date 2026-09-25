"""S11 -- pure RAS-mm -> glTF-space conversion. No randomness, no DB, no I/O.

**The central finding of this stage, verified empirically, not assumed:** GEOMETRY.md's
`LPS_TO_GLTF` matrix converts FROM DICOM LPS (x=Left+, y=Posterior+, z=Superior+). Every vertex
this pipeline has produced since S6 is already in **RAS** world mm (x=Right+, y=Anterior+,
z=Superior+) -- S6's affine, S7's geometry, S9's `index_vertices_to_world` all target RAS
explicitly. Applying GEOMETRY.md's literal matrix to RAS data silently flips X a SECOND time: a
synthetic RAS point at `(x=-5, y=10, z=-5)` (an "anterior" landmark, so y is large) maps to glTF
`x=-0.005` under the matrix below, and to `x=+0.005` under GEOMETRY.md's literal LPS matrix
applied to the same RAS data -- a left-right mirror, exactly the failure class CLAUDE.md's axis
conventions trap names. `RAS_TO_GLTF` here is GEOMETRY.md's own *semantic* intent (Left-> -Right,
Superior->Up, Posterior->Forward) re-expressed for RAS input, where Right is already positive and
Posterior is `-Anterior`:

    Right    -> Right    (no flip -- RAS already has it)
    Superior -> Up        (same axis in both conventions)
    Anterior -> -Forward, i.e. Posterior (-Anterior) -> +Forward

This is a proper rotation (determinant +1, verified and tested), not a reflection -- consistent
with "no mirroring" by construction, not just by absence of a bug.

**Centring units:** GEOMETRY.md's snippet converts to glTF metres, THEN centres -- but the
manifest field is named `centring_offset_mm` with millimetre-scale example values. Centring is
computed here in RAS mm, BEFORE the axis/scale transform, so the recorded offset is directly in
the units its name promises (same final geometry either order).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Right -> Right, Superior -> Up, Anterior -> -Forward (see module docstring).
RAS_TO_GLTF_3X3 = np.array(
    [
        [1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, -1.0, 0.0],
    ]
)
MM_TO_M = 0.001


@dataclass
class GltfTransformResult:
    vertices_gltf_m: np.ndarray
    normals_gltf: np.ndarray | None
    centring_offset_mm: np.ndarray  # (3,), the translation applied before axis conversion


def centring_offset_mm(vertices_ras_mm: np.ndarray) -> np.ndarray:
    """The translation that moves the vertices' axis-aligned bounding-box CENTRE (not the
    vertex mean) to the origin -- matches `trimesh.Trimesh.bounding_box.centroid`."""
    bbox_min = vertices_ras_mm.min(axis=0)
    bbox_max = vertices_ras_mm.max(axis=0)
    return np.asarray(-(bbox_min + bbox_max) / 2.0)


def to_gltf_space(
    vertices_ras_mm: np.ndarray, normals_ras: np.ndarray | None = None
) -> GltfTransformResult:
    """Centres (in RAS mm), converts axes, and scales to metres. Never mutates the inputs."""
    offset = centring_offset_mm(vertices_ras_mm)
    centred_mm = vertices_ras_mm + offset
    vertices_gltf_m = (centred_mm @ RAS_TO_GLTF_3X3.T) * MM_TO_M
    normals_gltf = None
    if normals_ras is not None:
        # a pure rotation (no translation, no scale) -- the same matrix applies to directions
        normals_gltf = normals_ras @ RAS_TO_GLTF_3X3.T
    return GltfTransformResult(
        vertices_gltf_m=vertices_gltf_m, normals_gltf=normals_gltf, centring_offset_mm=offset
    )
