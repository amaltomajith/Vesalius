"""S8 -- pure thresholding + morphological cleanup (GEOMETRY.md "Threshold presets" and
"Morphological cleanup", STAGES.md S8). No randomness, no DB, no I/O.

Order, verbatim from GEOMETRY.md (more specific than STAGES.md's "closing then largest
component", per the confirmed decision): threshold -> binary closing(2) -> binary opening(1) ->
largest connected component. `structure=None` (both here and in GEOMETRY.md's snippet) uses
scipy's default: a radius-1, face-connectivity ("cross") structuring element for each
closing/opening step, regardless of the `connectivity` used afterwards for `ndimage.label`.

**The border trap, considered and deliberately NOT "fixed":** `scipy.ndimage.binary_closing`/
`binary_opening` treat everything outside the array as background (`border_value=0`), so a real
structure that touches the volume's true edge can be eroded there by up to `iterations` voxels.
Two heuristics were tried to compensate -- edge-replication padding, and `border_value=1` on the
erosion half -- and both were rejected: replication invents tissue beyond the crop that may not
exist, and `border_value=1` also suppresses erosion at edges the CLOSING step's own dilation
pushed to the boundary (not just edges the real object always touched), silently over-preserving
more than the original object. Both are a heuristic about data outside the field of view --
exactly the "auto-repair" CLAUDE.md rule 6 prohibits. What ships here is the literal GEOMETRY.md
snippet: a structure touching the true array boundary may be eroded near that edge, and that is
an accepted, documented limitation, not a bug. It rarely matters in practice: S6's isotropic
resampling keeps the reconstruction circle away from the array edge.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage


@dataclass
class SegmentResult:
    mask: np.ndarray  # bool, same shape as the input volume
    stats: dict[str, object]


def _structure(connectivity: int) -> np.ndarray:
    if connectivity == 6:
        return ndimage.generate_binary_structure(3, 1)
    if connectivity == 26:
        return ndimage.generate_binary_structure(3, 3)
    raise ValueError(f"unsupported connectivity: {connectivity}")


def _clean(mask: np.ndarray, *, closing_iterations: int, opening_iterations: int) -> np.ndarray:
    """Closing then opening, GEOMETRY.md's literal order and scipy defaults (see the module
    docstring for why the true-edge erosion this can cause is left as a documented limitation
    rather than compensated for)."""
    result = mask
    if closing_iterations:
        result = ndimage.binary_closing(result, iterations=closing_iterations)
    if opening_iterations:
        result = ndimage.binary_opening(result, iterations=opening_iterations)
    return np.asarray(result, dtype=bool)


def segment(
    volume: np.ndarray,
    *,
    lo: float,
    hi: float,
    closing_iterations: int,
    opening_iterations: int,
    connectivity: int = 6,
) -> SegmentResult:
    """Never mutates `volume`. Returns the mask and per-step voxel counts (so a caller can
    answer "which step emptied it") -- it does not itself decide pass/fail; the stage wraps
    this with the sanity gates (empty / >90%) because those decisions carry reason codes that
    belong at the orchestration layer, matching S7's algorithm/service split."""
    thresholded = (volume >= lo) & (volume <= hi)
    threshold_voxels = int(thresholded.sum())

    cleaned = _clean(
        thresholded, closing_iterations=closing_iterations, opening_iterations=opening_iterations
    )
    cleaned_voxels = int(cleaned.sum())

    labels, n_components = ndimage.label(cleaned, structure=_structure(connectivity))
    largest_voxels = 0
    if n_components > 0:
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        largest_label = int(sizes.argmax())
        mask = np.asarray(labels == largest_label, dtype=bool)
        largest_voxels = int(sizes[largest_label])
    else:
        mask = np.zeros_like(cleaned)

    total_voxels = int(volume.size)
    bbox = None
    if mask.any():
        bbox = []
        for axes in ((1, 2), (0, 2), (0, 1)):
            idx = np.flatnonzero(mask.any(axis=axes))
            bbox.append([int(idx[0]), int(idx[-1])])

    stats: dict[str, object] = {
        "threshold_voxels": threshold_voxels,
        "cleaned_voxels": cleaned_voxels,
        "component_count": n_components,
        "largest_component_voxels": largest_voxels,
        "largest_component_fraction": (largest_voxels / cleaned_voxels) if cleaned_voxels else 0.0,
        "final_voxels": int(mask.sum()),
        "coverage_fraction": int(mask.sum()) / total_voxels if total_voxels else 0.0,
        "bbox_zyx": bbox,
    }
    return SegmentResult(mask=mask, stats=stats)
