"""Builds `meta.json`'s exact schema (S6 plan §6). DICOM.md Part 2: "Store the affine in
meta.json. Downstream stages must not re-derive it." -- this is the one place that
computation is recorded for S7+ to read.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import numpy as np

CONTRACT_NAME = "volume_contract"
CONTRACT_VERSION = "1.0"

GUARANTEES = (
    "de-identified per PS3.15 basic profile",
    "slice order validated by position projection",
    "spacing uniformity verified",
)


def build_meta_json(
    *,
    pipeline_version: str,
    stage_version: str,
    series_id: UUID,
    pseudo_series_uid: str,
    modality: str,
    shape: tuple[int, int, int],
    affine_ras_matrix: np.ndarray,
    target_spacing_mm: float,
    original_row_spacing_mm: float,
    original_column_spacing_mm: float,
    slice_spacing_mm: float,
    dicom_slice_thickness_mm: float | None,
    dicom_spacing_between_slices_mm: float | None,
    params_version: str,
    intensity_units: str,
    hu_semantics_valid: bool,
    rescale_slope: float,
    rescale_intercept: float,
    padding_value: float,
    instance_count: int,
    spacing_uniform: bool,
    orientation_consistent: bool,
    slice_order_pseudo_sop_uids: list[str],
    produced_by_run: UUID,
) -> dict[str, Any]:
    return {
        "contract_name": CONTRACT_NAME,
        "contract_version": CONTRACT_VERSION,
        "pipeline_version": pipeline_version,
        "stage_version": stage_version,
        "series_id": str(series_id),
        "pseudo_series_uid": pseudo_series_uid,
        "modality": modality,
        "array": {
            "axis_order": ["z", "y", "x"],
            "shape": list(shape),
            "dtype": "float32",
        },
        "spatial": {
            "coordinate_frame": "RAS",
            "spacing_mm": [target_spacing_mm, target_spacing_mm, target_spacing_mm],
            "isotropic": True,
            "affine": affine_ras_matrix.tolist(),
            "affine_source": "ImageOrientationPatient/ImagePositionPatient, first sorted slice",
            "original_spacing_mm": {
                "row": original_row_spacing_mm,
                "column": original_column_spacing_mm,
                "slice_derived": slice_spacing_mm,
            },
            "dicom_slice_thickness_mm": dicom_slice_thickness_mm,
            "dicom_spacing_between_slices_mm": dicom_spacing_between_slices_mm,
            "resampling": {
                "target_spacing_mm": target_spacing_mm,
                "interpolation": "linear",
                "params_version": params_version,
            },
        },
        "intensity": {
            "units": intensity_units,
            "hu_semantics_valid": hu_semantics_valid,
            "applies_to_modality": ["CT"],
            "rescale_applied": True,
            "rescale_slope": rescale_slope,
            "rescale_intercept": rescale_intercept,
            "air_value": -1000.0,
            "water_value": 0.0,
        },
        "nullability": {
            "padding_value": padding_value,
            "note": "Regions outside the reconstruction circle are padded, not missing",
        },
        "quality": {
            "sorted_by": "position_projection",
            "instance_count": instance_count,
            "spacing_uniform": spacing_uniform,
            "orientation_consistent": orientation_consistent,
            "slice_order_pseudo_sop_uids": slice_order_pseudo_sop_uids,
        },
        "guarantees": list(GUARANTEES),
        "produced_by_run": str(produced_by_run),
    }
