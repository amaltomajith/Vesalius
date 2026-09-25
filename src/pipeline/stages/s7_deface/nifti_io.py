"""NIfTI bytes <-> array for S7, with the S6 header lessons applied.

* SimpleITK works in LPS and converts to/from the file's RAS itself, so the defaced volume is
  written by copying the SOURCE image's geometry (`CopyInformation`) onto the edited array --
  never by rebuilding geometry from the RAS affine (which would be flipped a second time).
* A sitk write -> read round trip cannot expose a wrong header (ITK's LPS<->RAS flip is symmetric),
  so the header check compares raw bytes and parses the sform out of them (`parse_nifti_sform`).
* gzip mtime is 0 in ITK's writer, so identical arrays give byte-identical files (tested).
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import tempfile
from dataclasses import dataclass

import numpy as np
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library

from pipeline.stages.s6_volume_assembly.affine import parse_nifti_sform

NIFTI1_HEADER_BYTES = 348
# quatern_b/c/d, qoffset_x/y/z and srow_x/y/z: the fields ITK re-derives from a double-precision
# direction/spacing/origin, so an OBLIQUE (gantry-tilted) volume can come back a float32 ulp off.
_GEOMETRY_FIELDS = slice(256, 328)


@dataclass(frozen=True)
class HeaderComparison:
    exact: bool  # every one of the 348 bytes identical
    non_geometry_identical: bool  # every byte outside the quaternion/sform block identical
    geometry_close: bool  # quaternion + offset + sform floats equal within `tolerance`

    @property
    def equivalent(self) -> bool:
        return self.non_geometry_identical and self.geometry_close


def compare_headers(source: bytes, out: bytes, tolerance: float) -> HeaderComparison:
    """Exact equality is expected for axis-aligned volumes; for oblique ones only the geometry
    block may differ, and only by float rounding. Anything else is a different header."""
    n = _GEOMETRY_FIELDS
    src_geo = struct.unpack("<18f", source[n])
    out_geo = struct.unpack("<18f", out[n])
    close = all(abs(a - b) <= tolerance for a, b in zip(src_geo, out_geo, strict=True))
    return HeaderComparison(
        exact=source == out,
        non_geometry_identical=source[: n.start] == out[: n.start]
        and source[n.stop :] == out[n.stop :],
        geometry_close=close,
    )


@dataclass
class LoadedVolume:
    image: sitk.Image
    array: np.ndarray  # (z, y, x) float32, a private writable copy
    header: bytes  # first 348 decompressed bytes, as stored
    sform: np.ndarray  # 4x4 RAS affine parsed from `header`


def header_bytes(nii_gz: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(nii_gz)) as fh:
        header = fh.read(NIFTI1_HEADER_BYTES)
    if len(header) != NIFTI1_HEADER_BYTES:
        raise ValueError(f"NIfTI header truncated ({len(header)} bytes)")
    return header


def load_volume(nii_gz: bytes) -> LoadedVolume:
    header = header_bytes(nii_gz)
    sform = parse_nifti_sform(header)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "volume.nii.gz")
        with open(path, "wb") as fh:
            fh.write(nii_gz)
        image = sitk.ReadImage(path)
    array = np.array(sitk.GetArrayFromImage(image), dtype=np.float32, copy=True)
    return LoadedVolume(image=image, array=array, header=header, sform=sform)


def write_like(source: sitk.Image, array: np.ndarray) -> bytes:
    """Serialise `array` (z, y, x) with `source`'s exact geometry."""
    image = sitk.GetImageFromArray(np.ascontiguousarray(array, dtype=np.float32))
    image.CopyInformation(source)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "volume_defaced.nii.gz")
        sitk.WriteImage(image, path)
        with open(path, "rb") as fh:
            return fh.read()
