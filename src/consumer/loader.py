"""`load(volume, contract)` -- validate first, return second. The Proof 4 entry point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from consumer import nifti
from consumer.contract import ContractViolation, validate_contract, validate_manifest

ORTHONORMAL_TOL = 1e-3
ISOTROPY_TOL = 1e-3


@dataclass(frozen=True)
class Volume:
    array: np.ndarray  # (z, y, x) float32, Hounsfield units
    affine_ras: np.ndarray  # 4x4: (i, j, k, 1) -> RAS mm, with i=x-index, j=y-index, k=z-index
    spacing_mm: tuple[float, float, float]  # (x, y, z)


def load(
    volume_nii_gz: bytes, contract: dict[str, Any], manifest: dict[str, Any] | None = None
) -> Volume:
    validate_contract(contract)
    if manifest is not None:
        validate_manifest(contract, manifest)

    raw = nifti.gunzip(volume_nii_gz)
    h = nifti.read_header(raw)

    if len(h.dim) != contract["array"]["dimensions"]:
        raise ContractViolation(f"expected 3 dimensions, got {len(h.dim)}")
    if h.datatype != nifti.FLOAT32 or contract["array"]["dtype"] != "float32":
        raise ContractViolation(
            f"expected float32 (contract declares {contract['array']['dtype']!r}), "
            f"header datatype code is {h.datatype}"
        )
    if h.xyzt_units & 7 != nifti.UNITS_MM:
        raise ContractViolation("expected spatial units of millimetres")
    if h.sform_code <= 0:
        raise ContractViolation("volume carries no sform, so its RAS orientation is unknown")

    rotation = h.sform[:3, :3]
    lengths = np.linalg.norm(rotation, axis=0)
    if np.any(lengths <= 0):
        raise ContractViolation("sform has a zero-length axis")
    unit = rotation / lengths
    if not np.allclose(unit.T @ unit, np.eye(3), atol=ORTHONORMAL_TOL):
        raise ContractViolation("sform axes are not orthonormal (sheared or corrupt geometry)")
    if np.linalg.det(unit) <= 0:
        raise ContractViolation(
            "sform is a reflection (determinant < 0): the volume is mirrored relative to RAS"
        )
    if contract["spatial"]["isotropic"] and (lengths.max() - lengths.min()) > ISOTROPY_TOL:
        raise ContractViolation(f"contract says isotropic but voxel sizes are {lengths.tolist()}")
    if not np.allclose(lengths, h.pixdim, atol=1e-2):
        raise ContractViolation("pixdim disagrees with the sform's axis lengths")

    array = nifti.read_float32(raw, h)
    if not np.isfinite(array).all():
        raise ContractViolation("volume contains non-finite values")
    return Volume(
        array=array,
        affine_ras=h.sform,
        spacing_mm=(float(lengths[0]), float(lengths[1]), float(lengths[2])),
    )
