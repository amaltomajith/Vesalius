"""S8's own mask writer. Reading the gated intensity volume reuses `s7_deface.nifti_io.load_volume`
(generic NIfTI I/O, not S7-specific logic -- same status as `storage.object_store.get_bytes`).
Writing is NOT reused: a mask is uint8 0/1, not the float32 intensity `write_like` produces, so
this is a small, deliberate, single-purpose duplicate (S6/S7 precedent: leave stable one-off
duplicates alone rather than retrofit a shared helper's contract).

The header CHECK is also its own function, not S7's `compare_headers`: S7 rewrites the SAME
dtype (float32 -> float32), so it can require near-total byte equality. S8's mask is uint8 --
`datatype`/`bitpix`/`scl_slope` etc. are SUPPOSED to differ -- so this compares only the fields
that must still agree: voxel dimensions, spacing, and the qform/sform geometry block.
"""

from __future__ import annotations

import os
import struct
import tempfile
from dataclasses import dataclass

import numpy as np
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library

# NIfTI-1 header offsets (little-endian): dim[1..3] (voxel counts), pixdim[1..3] (spacing),
# qform_code+sform_code, then quatern_b/c/d + qoffset_x/y/z + srow_x/y/z (the same block S7's
# `compare_headers` treats as float geometry, here always compared with tolerance).
_DIM = slice(42, 48)
_PIXDIM = slice(80, 92)
_CODES = slice(252, 256)
_GEOMETRY_FLOATS = slice(256, 328)


@dataclass(frozen=True)
class GeometryComparison:
    dims_match: bool
    pixdim_close: bool
    codes_match: bool
    geometry_close: bool

    @property
    def equivalent(self) -> bool:
        return self.dims_match and self.pixdim_close and self.codes_match and self.geometry_close


def compare_geometry(
    source_header: bytes, output_header: bytes, tolerance: float
) -> GeometryComparison:
    src_pixdim = struct.unpack("<3f", source_header[_PIXDIM])
    out_pixdim = struct.unpack("<3f", output_header[_PIXDIM])
    src_geo = struct.unpack("<18f", source_header[_GEOMETRY_FLOATS])
    out_geo = struct.unpack("<18f", output_header[_GEOMETRY_FLOATS])
    return GeometryComparison(
        dims_match=source_header[_DIM] == output_header[_DIM],
        pixdim_close=all(
            abs(a - b) <= tolerance for a, b in zip(src_pixdim, out_pixdim, strict=True)
        ),
        codes_match=source_header[_CODES] == output_header[_CODES],
        geometry_close=all(abs(a - b) <= tolerance for a, b in zip(src_geo, out_geo, strict=True)),
    )


def write_mask(source: sitk.Image, mask: np.ndarray) -> bytes:
    """Serialise a boolean/0-1 `mask` (z, y, x) as uint8 with `source`'s exact geometry."""
    image = sitk.GetImageFromArray(np.ascontiguousarray(mask, dtype=np.uint8))
    image.CopyInformation(source)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mask.nii.gz")
        sitk.WriteImage(image, path)
        with open(path, "rb") as fh:
            return fh.read()


def load_mask(nii_gz: bytes) -> np.ndarray:
    """Read a mask back as bool (z, y, x)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "mask.nii.gz")
        with open(path, "wb") as fh:
            fh.write(nii_gz)
        image = sitk.ReadImage(path)
    return np.asarray(sitk.GetArrayFromImage(image), dtype=bool)
