"""Builds `manifest.json`'s schema (GEOMETRY.md "Manifest"), with two corrections carried
forward from `gltf_transform.py`'s own findings, applied here rather than silently:

1. **`geometry.source_frame` is `"RAS_mm"`, not GEOMETRY.md's literal example `"LPS_mm"`.**
   Every vertex this pipeline has produced since S6 is RAS-native (see `gltf_transform.py`'s
   module docstring) -- recording `"LPS_mm"` here would mislabel this pipeline's own data in the
   one document meant to make it legible to a downstream reader or Proof 3's rebuild check.
2. **`parameters` is nested by producing stage** (`segment`, `surface_extraction`,
   `mesh_postprocess`), not flattened into GEOMETRY.md's illustrative single-level shape
   (`threshold_hu`, `step_size`, `smoothing: {...}` at the top). Three different stages each
   independently version their own numbers (S8's `thresholds.min/max`, S9's `level`/`step_size`,
   S10's `taubin_*`/`target_faces`); flattening them under GEOMETRY.md's example key names would
   require renaming/guessing at a mapping GEOMETRY.md never actually specifies, and risks silently
   misrepresenting which stage chose which number. Carrying each stage's own `algorithm_params()`
   block forward VERBATIM (same discipline as the `deface` block's carry-forward since S9->S10)
   keeps every value traceable to the `transform_run` that actually produced it.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

SOURCE_FRAME = "RAS_mm"
EXPORT_FRAME = "gltf_Y_up_m"


def build_manifest(
    *,
    contract_version: str,
    pipeline_version: str,
    series_id: UUID,
    preset: str,
    glb_ref: dict[str, Any],  # {"sha256", "bytes", "faces"}
    volume_ref: dict[str, Any],  # {"sha256", "bytes"}
    thumbnail_ref: dict[str, Any],  # {"sha256", "bytes"}
    centring_offset_mm: list[float],
    voxel_spacing_mm: list[float],
    segment_params: dict[str, Any],
    surface_extraction_params: dict[str, Any],
    mesh_postprocess_params: dict[str, Any],
    deface_block: dict[str, Any],
) -> bytes:
    manifest = {
        "contract_version": contract_version,
        "pipeline_version": pipeline_version,
        "series_id": str(series_id),
        "artifacts": {
            "surface.glb": glb_ref,
            "volume.nii.gz": volume_ref,
            "thumbnail.png": thumbnail_ref,
        },
        "geometry": {
            "source_frame": SOURCE_FRAME,
            "export_frame": EXPORT_FRAME,
            "centring_offset_mm": centring_offset_mm,
            "voxel_spacing_mm": voxel_spacing_mm,
        },
        "parameters": {
            "preset": preset,
            "segment": segment_params,
            "surface_extraction": surface_extraction_params,
            "mesh_postprocess": mesh_postprocess_params,
        },
        "deface": deface_block,
    }
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
