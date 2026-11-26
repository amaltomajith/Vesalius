"""S9 structures mode -- pure per-structure marching cubes (refs/INTEGRATION.md §2, S9).

"Marching cubes runs once per structure mask, not once per volume. Each output carries its
label. For masks, pass the mask through a light Gaussian blur first and mesh at level 0.5 --
marching cubes on a hard binary mask produces blocky stair-stepping."

Each structure is meshed inside its own bounding box (padded so the blur and the isosurface can
close), and the crop offset is added back BEFORE the one full-affine index->world multiply
(`index_vertices_to_world`, the S6/S7-verified convention). No DB, no I/O, no randomness.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage
from skimage import measure

from pipeline.stages.s9_surface_extraction.mesh_extract import index_vertices_to_world
from pipeline.stages.s9_surface_extraction.structure_mesh_io import StructureMesh

STRUCTURES_PARAMS_VERSION = "2"
# The blur is sized in millimetres, against the SEGMENTATION's resolution, not the volume's:
# TotalSegmentator `--fast` segments on a 3 mm grid and hands the labels back on the input grid,
# so every mask is built from 3 mm blocks. v1 blurred by one voxel of the (1 mm) input grid, which
# left the 3 mm staircase in every surface. Half the segmentation grid smooths the steps while
# keeping ribs and vessels (several mm across) intact. Never below one voxel.
BLUR_SIGMA_MM = 1.5
LEVEL = 0.5
PAD_VOXELS = 3
STEP_SIZE = 1


def structures_algorithm_params() -> dict[str, object]:
    return {
        "mode": "structures",
        "structures_params_version": STRUCTURES_PARAMS_VERSION,
        "blur_sigma_mm": BLUR_SIGMA_MM,
        "level": LEVEL,
        "pad_voxels": PAD_VOXELS,
        "step_size": STEP_SIZE,
    }


def blur_sigma_voxels(affine_ras: np.ndarray) -> tuple[float, float, float]:
    """BLUR_SIGMA_MM per array axis (k, j, i). Affine columns are (i, j, k), so the voxel size of
    array axis 0 is the norm of column 2."""
    size_ijk = np.linalg.norm(np.asarray(affine_ras)[:3, :3], axis=0)
    size_kji = size_ijk[::-1]
    return tuple(max(1.0, BLUR_SIGMA_MM / float(s)) for s in size_kji)  # type: ignore[return-value]


def mesh_structure(
    labels: np.ndarray, label_id: int, affine_ras: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """One structure -> (vertices RAS mm, faces). Raises ValueError when no surface exists."""
    sel = labels == label_id
    boxes = ndimage.find_objects(sel.astype(np.uint8))
    if not boxes or boxes[0] is None:
        raise ValueError("label absent")
    lo = [max(0, s.start - PAD_VOXELS) for s in boxes[0]]
    hi = [min(n, s.stop + PAD_VOXELS) for s, n in zip(boxes[0], labels.shape, strict=True)]
    crop = sel[lo[0] : hi[0], lo[1] : hi[1], lo[2] : hi[2]].astype(np.float32)
    # zero-pad so a structure touching the volume edge still closes instead of opening a hole
    crop = np.pad(crop, 1, mode="constant")
    field = ndimage.gaussian_filter(crop, sigma=blur_sigma_voxels(affine_ras))
    if float(field.max()) <= LEVEL:
        raise ValueError("blurred mask never reaches the level (structure too thin or small)")
    verts, faces, _n, _v = measure.marching_cubes(
        field, level=LEVEL, step_size=STEP_SIZE, allow_degenerate=False
    )
    if len(faces) == 0:
        raise ValueError("zero faces")
    verts_kji = verts - 1.0 + np.asarray(lo, dtype=float)  # undo the pad, add the crop origin
    return index_vertices_to_world(verts_kji, affine_ras), faces.astype(np.int64)


def mesh_all(
    labels: np.ndarray, affine_ras: np.ndarray, structures: list[dict[str, object]]
) -> tuple[list[StructureMesh], list[dict[str, str]]]:
    """Meshes every structure listed by S8. Returns (meshes, not_meshed) -- a structure that
    yields no surface is reported with its reason, never silently dropped."""
    meshes: list[StructureMesh] = []
    not_meshed: list[dict[str, str]] = []
    for s in structures:
        node = str(s["node"])
        try:
            v, f = mesh_structure(labels, int(s["label_id"]), affine_ras)  # type: ignore[call-overload]
        except ValueError as exc:
            not_meshed.append({"node": node, "reason": str(exc)})
            continue
        meshes.append(StructureMesh(node=node, vertices_ras=v, faces=f))
    return meshes, not_meshed
