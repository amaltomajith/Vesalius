"""S8's own NIfTI I/O: the mask is written as uint8 (not S7's float32), and its header check
compares only the fields that must still agree between a float32 source and a uint8 mask
(dims, spacing, qform/sform codes, the geometry floats) -- verified directly against real
headers, not just observed indirectly through a happy path where every field already agrees.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

from pipeline.stages.s7_deface.nifti_io import header_bytes
from pipeline.stages.s8_segment import nifti_io as mask_io
from tests.fixtures.deface_runs import nifti_bytes

# NIfTI-1 offsets (little-endian): datatype (short), bitpix (short)
_DATATYPE = slice(70, 72)
_BITPIX = slice(72, 74)
DT_UINT8 = 2
DT_FLOAT32 = 16


def _source_and_header() -> tuple[object, bytes]:
    import SimpleITK as sitk  # noqa: N813

    arr = np.zeros((6, 7, 8), dtype=np.float32)
    affine = np.eye(4)
    affine[:3, 3] = (3.0, 4.0, 5.0)
    src_bytes = nifti_bytes(arr, affine)
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "src.nii.gz")
        with open(path, "wb") as fh:
            fh.write(src_bytes)
        image = sitk.ReadImage(path)
    return image, header_bytes(src_bytes)


def test_a_mask_is_written_as_uint8_not_float32_on_disk() -> None:
    source, src_header = _source_and_header()
    mask = np.zeros((6, 7, 8), dtype=bool)
    mask[2:4, 2:4, 2:4] = True

    out = mask_io.write_mask(source, mask)
    out_header = header_bytes(out)

    (out_datatype,) = struct.unpack("<h", out_header[_DATATYPE])
    (out_bitpix,) = struct.unpack("<h", out_header[_BITPIX])
    assert out_datatype == DT_UINT8
    assert out_bitpix == 8

    (src_datatype,) = struct.unpack("<h", src_header[_DATATYPE])
    assert src_datatype == DT_FLOAT32  # the source really is float32 -- the two genuinely differ


def test_write_then_load_round_trips_the_mask_exactly() -> None:
    source, _ = _source_and_header()
    rng = np.random.default_rng(1)
    mask = rng.random((6, 7, 8)) > 0.5
    out = mask_io.write_mask(source, mask)
    assert np.array_equal(mask_io.load_mask(out), mask)


def test_compare_geometry_passes_two_headers_that_actually_agree() -> None:
    _, header = _source_and_header()
    cmp = mask_io.compare_geometry(header, header, 1e-3)
    assert cmp.equivalent
    assert cmp.dims_match and cmp.pixdim_close and cmp.codes_match and cmp.geometry_close


@pytest.mark.parametrize(
    "offset, delta, field",
    [
        (280, 5.0, "geometry_close"),  # srow_x[0]: the sform rotation/scale
        (292, 5.0, "geometry_close"),  # srow_x[3]: the sform translation (origin)
        (296, 5.0, "geometry_close"),  # srow_y[0]
    ],
)
def test_compare_geometry_catches_a_shifted_sform(offset: int, delta: float, field: str) -> None:
    _, header = _source_and_header()
    (value,) = struct.unpack("<f", header[offset : offset + 4])
    moved = bytearray(header)
    moved[offset : offset + 4] = struct.pack("<f", value + delta)
    cmp = mask_io.compare_geometry(header, bytes(moved), 1e-3)
    assert not getattr(cmp, field)
    assert not cmp.equivalent
    # nothing else about the comparison should have moved
    assert cmp.dims_match and cmp.pixdim_close and cmp.codes_match


def test_compare_geometry_catches_a_changed_voxel_dimension() -> None:
    _, header = _source_and_header()
    (nx,) = struct.unpack("<h", header[42:44])
    moved = bytearray(header)
    moved[42:44] = struct.pack("<h", nx + 1)
    cmp = mask_io.compare_geometry(header, bytes(moved), 1e-3)
    assert not cmp.dims_match
    assert not cmp.equivalent
    assert cmp.pixdim_close and cmp.codes_match and cmp.geometry_close


def test_compare_geometry_catches_a_changed_spacing() -> None:
    _, header = _source_and_header()
    (dx,) = struct.unpack("<f", header[80:84])
    moved = bytearray(header)
    moved[80:84] = struct.pack("<f", dx + 1.0)
    cmp = mask_io.compare_geometry(header, bytes(moved), 1e-3)
    assert not cmp.pixdim_close
    assert not cmp.equivalent
    assert cmp.dims_match and cmp.codes_match and cmp.geometry_close


def test_compare_geometry_catches_a_changed_sform_code() -> None:
    _, header = _source_and_header()
    (code,) = struct.unpack("<h", header[254:256])
    moved = bytearray(header)
    moved[254:256] = struct.pack("<h", 0 if code != 0 else 1)
    cmp = mask_io.compare_geometry(header, bytes(moved), 1e-3)
    assert not cmp.codes_match
    assert not cmp.equivalent


def test_compare_geometry_tolerates_a_sub_tolerance_float_difference() -> None:
    _, header = _source_and_header()
    (value,) = struct.unpack("<f", header[280:284])
    moved = bytearray(header)
    moved[280:284] = struct.pack("<f", value + 1e-5)
    cmp = mask_io.compare_geometry(header, bytes(moved), 1e-3)
    assert cmp.equivalent


def test_compare_geometry_ignores_datatype_and_bitpix_which_are_meant_to_differ() -> None:
    """The mask's whole point is a different dtype from the source -- the comparison must not
    flag that on its own."""
    _, header = _source_and_header()
    moved = bytearray(header)
    moved[_DATATYPE] = struct.pack("<h", DT_UINT8)
    moved[_BITPIX] = struct.pack("<h", 8)
    cmp = mask_io.compare_geometry(header, bytes(moved), 1e-3)
    assert cmp.equivalent
