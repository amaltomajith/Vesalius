"""S6's most dangerous failure class: a volume that is mirrored, axis-swapped, or carries an
affine in the wrong coordinate frame -- and looks perfectly plausible. TESTING.md Part 4
names this file for "LPS -> RAS -> glTF round trip"; the glTF leg belongs to S11, so this
covers the DICOM LPS -> NIfTI RAS leg, at three independent layers:

1. pure affine math against hand-computed matrices,
2. real SimpleITK conventions (Direction column order, LPS physical space, and what its NIfTI
   writer actually puts in the header),
3. the REAL `_assemble` code path against a marker fixture, asserting on both the voxel
   array AND the affine a RAS consumer reads from the raw header bytes.

Why layer 3 checks two things: array indices are untouched by header flips, and a sitk
write->read round trip is blind to a header error (ITK converts LPS<->RAS symmetrically on
read). A previous version of S6 pre-flipped its geometry to RAS, so ITK flipped a second time
on write and left LPS-valued numbers in a header labelled RAS -- and every check that went
through the array or through sitk.ReadImage passed anyway. Only parsing the header bytes
sees it.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from uuid import uuid4

import numpy as np
import pydicom
import pytest
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library

from pipeline.common.config import Settings
from pipeline.stages.s6_volume_assembly.affine import (
    LPS_TO_RAS,
    affine_lps,
    affine_ras,
    read_nifti_sform,
)
from pipeline.stages.s6_volume_assembly.service import _assemble, _direction_tuple
from tests.fixtures.dicom_factory import build_asymmetric_marker_series, build_series
from tests.fixtures.volume_asserts import make_in_memory_series, read_volume

# ------------------------------------------------------------------ pure affine math


def test_lps_to_ras_is_exactly_the_documented_sign_flip() -> None:
    assert np.array_equal(LPS_TO_RAS, np.diag([-1.0, -1.0, 1.0, 1.0]))


def test_affine_lps_matches_hand_computation_for_a_rotated_orientation() -> None:
    # row_cosine=(0,1,0), col_cosine=(-1,0,0)  =>  normal = row x col = (0,0,1).
    # PixelSpacing is [row, col] = [dy, dx]; the affine's x column carries the COLUMN spacing.
    a = affine_lps(
        row_cosine=(0, 1, 0),
        col_cosine=(-1, 0, 0),
        normal=(0, 0, 1),
        dx=0.7,
        dy=0.8,
        spacing_z=2.5,
        origin=(10, 20, 30),
    )
    expected = np.array(
        [
            [0.0, -0.8, 0.0, 10.0],
            [0.7, 0.0, 0.0, 20.0],
            [0.0, 0.0, 2.5, 30.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
    )
    assert np.allclose(a, expected)

    # voxel (col i=2, row j=3, slice k=4): x = -0.8*3 + 10, y = 0.7*2 + 20, z = 2.5*4 + 30
    lps = a @ np.array([2, 3, 4, 1.0])
    assert np.allclose(lps, [7.6, 21.4, 40.0, 1.0])
    assert np.allclose(affine_ras(a) @ np.array([2, 3, 4, 1.0]), [-7.6, -21.4, 40.0, 1.0])


# ------------------------------------------------------------------ real SimpleITK behaviour


def test_direction_tuple_puts_the_axis_vectors_in_matrix_columns() -> None:
    d = _direction_tuple(np.array([1.0, 2, 3]), np.array([4.0, 5, 6]), np.array([7.0, 8, 9]))
    assert d == (1, 4, 7, 2, 5, 8, 3, 6, 9)
    m = np.array(d).reshape(3, 3)
    assert np.array_equal(m[:, 0], [1, 2, 3])
    assert np.array_equal(m[:, 1], [4, 5, 6])
    assert np.array_equal(m[:, 2], [7, 8, 9])


_ROT_ROW = np.array([0.0, 1.0, 0.0])
_ROT_COL = np.array([-1.0, 0.0, 0.0])
_ROT_NORMAL = np.cross(_ROT_ROW, _ROT_COL)
_DX, _DY, _DZ = 0.7, 0.8, 2.5
_ROT_ORIGIN = np.array([10.0, 20.0, 30.0])


def _rotated_image() -> sitk.Image:
    image = sitk.GetImageFromArray(np.zeros((3, 4, 5), dtype=np.float32))  # size (x=5,y=4,z=3)
    image.SetDirection(_direction_tuple(_ROT_ROW, _ROT_COL, _ROT_NORMAL))
    image.SetSpacing((_DX, _DY, _DZ))
    image.SetOrigin(tuple(_ROT_ORIGIN))
    return image


def test_simpleitk_voxel_to_world_agrees_with_the_dicom_affine() -> None:
    a = affine_lps(_ROT_ROW, _ROT_COL, _ROT_NORMAL, _DX, _DY, _DZ, _ROT_ORIGIN)
    image = _rotated_image()
    for idx in [(0, 0, 0), (2, 3, 1), (4, 3, 2), (1, 0, 2)]:
        expected = (a @ np.array([*idx, 1.0]))[:3]
        assert np.allclose(image.TransformIndexToPhysicalPoint(idx), expected)


def test_naive_direction_concatenation_is_the_transpose_and_lands_elsewhere() -> None:
    # The trap the plan's pseudocode fell into: (*row, *col, *normal) fills matrix ROWS.
    a = affine_lps(_ROT_ROW, _ROT_COL, _ROT_NORMAL, _DX, _DY, _DZ, _ROT_ORIGIN)
    image = _rotated_image()
    image.SetDirection((*_ROT_ROW, *_ROT_COL, *_ROT_NORMAL))
    expected = (a @ np.array([2, 3, 1, 1.0]))[:3]
    assert not np.allclose(image.TransformIndexToPhysicalPoint((2, 3, 1)), expected)


def _write(image: sitk.Image) -> str:
    fd, path = tempfile.mkstemp(suffix=".nii.gz")
    os.close(fd)
    sitk.WriteImage(image, path)
    return path


def test_written_header_is_ras_and_equals_the_dicom_derived_affine() -> None:
    a_lps = affine_lps(_ROT_ROW, _ROT_COL, _ROT_NORMAL, _DX, _DY, _DZ, _ROT_ORIGIN)
    path = _write(_rotated_image())  # DICOM-native LPS geometry, as S6 builds it
    try:
        assert np.allclose(read_nifti_sform(path), affine_ras(a_lps), atol=1e-5)
    finally:
        os.remove(path)


def test_preflipping_to_ras_is_invisible_to_a_sitk_roundtrip_but_caught_by_the_header() -> None:
    """The exact bug an earlier S6 shipped. ITK flips LPS->RAS on write and back on read, so
    handing it pre-flipped geometry survives a sitk round trip unchanged while the header on
    disk ends up carrying LPS-valued numbers. This asserts BOTH halves so the blind spot is
    documented: the round trip cannot tell, the header parse can.
    """
    flip = np.array([-1.0, -1.0, 1.0])
    image = sitk.GetImageFromArray(np.zeros((3, 4, 5), dtype=np.float32))
    image.SetDirection(_direction_tuple(_ROT_ROW * flip, _ROT_COL * flip, _ROT_NORMAL * flip))
    image.SetSpacing((_DX, _DY, _DZ))
    image.SetOrigin(tuple(_ROT_ORIGIN * flip))
    path = _write(image)
    try:
        reread = sitk.ReadImage(path)
        assert np.allclose(reread.GetOrigin(), _ROT_ORIGIN * flip)  # round trip: "looks fine"
        correct_ras = affine_ras(
            affine_lps(_ROT_ROW, _ROT_COL, _ROT_NORMAL, _DX, _DY, _DZ, _ROT_ORIGIN)
        )
        assert not np.allclose(read_nifti_sform(path), correct_ras, atol=1e-5)  # header: wrong
    finally:
        os.remove(path)


# ------------------------------------------------------------------ the real _assemble path


def _assemble_series(blobs: list[bytes], **series_kwargs):  # type: ignore[no-untyped-def]
    mem = make_in_memory_series(blobs, **series_kwargs)
    result = _assemble(mem.client, Settings(), mem.series, mem.artifacts_by_instance, uuid4())
    return mem, result, read_volume(result.volume_bytes)


def test_marker_fixture_really_has_reversed_instance_numbers() -> None:
    """Guards the guard: if someone 'tidies' the fixture so InstanceNumber ascends with
    position, the sort-by-InstanceNumber regression below would silently stop being caught.
    """
    blobs = build_asymmetric_marker_series()
    datasets = [pydicom.dcmread(io.BytesIO(b)) for b in blobs]
    z = [float(d.ImagePositionPatient[2]) for d in datasets]
    numbers = [int(d.InstanceNumber) for d in datasets]
    assert z == sorted(z)
    assert numbers == sorted(numbers, reverse=True)


def test_marker_lands_at_the_expected_voxel_and_nowhere_else() -> None:
    _, result, vol = _assemble_series(build_asymmetric_marker_series())

    assert vol.array.shape == (5, 8, 7)  # (z, y, x): all three dims distinct, so a swap shows
    assert vol.array.dtype == np.float32

    expected = np.zeros((5, 8, 7), dtype=np.float32)
    expected[4, 1, 5] = 3000.0
    assert np.allclose(vol.array, expected, atol=1e-3)  # the whole volume, not just one point

    # Each of these is the index a *specific* bug class would have put the marker at:
    assert vol.array[0, 1, 5] != pytest.approx(3000.0)  # sorted by InstanceNumber (z flipped)
    assert vol.array[4, 6, 5] != pytest.approx(3000.0)  # row axis mirrored: 8 - 1 - 1
    assert vol.array[4, 1, 1] != pytest.approx(3000.0)  # column axis mirrored: 7 - 1 - 5
    assert vol.array[4, 5, 1] != pytest.approx(3000.0)  # row/column transposed
    assert result.shape == (5, 8, 7)


def test_marker_voxel_has_the_expected_world_coordinate_in_the_written_header() -> None:
    _, _, vol = _assemble_series(build_asymmetric_marker_series())

    # marker voxel: column i=5, row j=1, slice k=4  ->  DICOM LPS (5, 1, 4)  ->  RAS (-5, -1, 4)
    ras = vol.sform @ np.array([5, 1, 4, 1.0])
    assert np.allclose(ras, [-5.0, -1.0, 4.0, 1.0], atol=1e-4)
    # ...and ITK's own (LPS) view of the same voxel is the DICOM coordinate:
    assert np.allclose(vol.itk_image.TransformIndexToPhysicalPoint((5, 1, 4)), [5.0, 1.0, 4.0])
    assert np.allclose(vol.sform[:3, :3], np.diag([-1.0, -1.0, 1.0]), atol=1e-6)


def test_meta_json_records_the_same_affine_as_the_header_and_the_position_sorted_order() -> None:
    blobs = build_asymmetric_marker_series()
    mem, result, vol = _assemble_series(blobs)
    meta = json.loads(result.meta_bytes)

    assert np.allclose(np.array(meta["spatial"]["affine"]), vol.sform, atol=1e-5)
    assert meta["spatial"]["coordinate_frame"] == "RAS"
    assert meta["array"]["axis_order"] == ["z", "y", "x"]
    assert meta["array"]["shape"] == [5, 8, 7]

    by_z = [str(pydicom.dcmread(io.BytesIO(b)).SOPInstanceUID) for b in blobs]
    assert meta["quality"]["slice_order_pseudo_sop_uids"] == by_z  # blobs are built in z order


# Hand-computed expectations, deliberately written out as literals rather than derived with
# the code under test. Each case: (IOP, first-slice IPP, per-slice step, PixelSpacing,
# expected RAS affine at the 1.0mm isotropic target).
_OBLIQUE_CASES = {
    # 90 degree in-plane rotation, anisotropic voxels, offset origin.
    #   LPS cols: (0,1,0), (-1,0,0), (0,0,1), origin (10,20,5)  ->  flip x,y rows for RAS
    "in_plane_rotation": (
        [0, 1, 0, -1, 0, 0],
        (10.0, 20.0, 5.0),
        (0.0, 0.0, 2.5),
        (0.5, 0.5),
        np.array(
            [
                [0.0, 1.0, 0.0, -10.0],
                [-1.0, 0.0, 0.0, -20.0],
                [0.0, 0.0, 1.0, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        ),
    ),
    # Gantry-tilt style: col_cosine tipped out of the axial plane (3-4-5 triangle), so the
    # slice normal (0,-0.8,0.6) is not axis-aligned and IPP moves in y AND z per slice.
    "tilted_normal": (
        [1, 0, 0, 0, 0.6, 0.8],
        (5.0, 7.0, 9.0),
        (0.0, -1.6, 1.2),
        (0.5, 0.5),
        np.array(
            [
                [-1.0, 0.0, 0.0, -5.0],
                [0.0, -0.6, 0.8, -7.0],
                [0.0, 0.8, 0.6, 9.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        ),
    ),
    # Normal points -z (feet-first style): position projects to -z, so ascending projection
    # is DESCENDING physical z. Origin must be the first slice in projection order.
    "flipped_normal": (
        [1, 0, 0, 0, -1, 0],
        (0.0, 0.0, 10.0),
        (0.0, 0.0, -2.0),
        (1.0, 1.0),
        np.array(
            [
                [-1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, -1.0, 10.0],
                [0.0, 0.0, 0.0, 1.0],
            ]
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(_OBLIQUE_CASES))
def test_oblique_orientations_write_the_hand_computed_ras_affine(case: str) -> None:
    iop, ipp0, step, spacing, expected_ras = _OBLIQUE_CASES[case]
    count = 4
    overrides = {
        k: {
            "image_position": (
                ipp0[0] + k * step[0],
                ipp0[1] + k * step[1],
                ipp0[2] + k * step[2],
            ),
            # descending InstanceNumber again, so a sort-by-InstanceNumber regression also fails
            "instance_number": count - k,
        }
        for k in range(count)
    }
    blobs = build_series(
        count=count,
        overrides=overrides,
        orientation=iop,
        pixel_spacing=spacing,
        rows=8,
        columns=6,
        pixel_data="valid",
        modality="CT",
    )
    slice_spacing = float(np.linalg.norm(step))
    _, _, vol = _assemble_series(blobs, slice_spacing=slice_spacing)

    assert np.allclose(vol.sform, expected_ras, atol=1e-4), (
        f"{case}: on-disk sform\n{vol.sform}\n!= hand-computed RAS affine\n{expected_ras}"
    )
    # ITK's LPS view must agree with the same hand-computed affine (LPS = flip of RAS):
    expected_lps = LPS_TO_RAS @ expected_ras
    for idx in [(0, 0, 0), (1, 2, 1), (2, 3, 4)]:
        got = np.array(vol.itk_image.TransformIndexToPhysicalPoint(idx))
        assert np.allclose(got, (expected_lps @ np.array([*idx, 1.0]))[:3], atol=1e-4)


def test_anisotropic_pixel_spacing_is_row_then_column_not_x_then_y() -> None:
    """CLAUDE.md trap #2: PixelSpacing is [row, column] = [y, x]. Symmetric fixtures cannot
    see a swap, so this one uses 0.5mm between rows and 1.0mm between columns.

    Chosen so the 1.0mm output grid lands exactly on original sample nodes (rows 0,2,4,6 at
    0,1,2,3mm; columns at every mm) -- the expected volume is then exact, with no
    interpolation blur, and a row/column spacing swap changes both the shape and where the
    marker ends up.
    """
    blobs = build_asymmetric_marker_series(
        rows=8, columns=6, marker_row=2, marker_col=4, pixel_spacing=(0.5, 1.0)
    )
    _, result, vol = _assemble_series(blobs)

    # y extent = 8 rows * 0.5mm = 4 voxels; x extent = 6 columns * 1.0mm = 6 voxels
    assert vol.array.shape == (5, 4, 6)
    expected = np.zeros((5, 4, 6), dtype=np.float32)
    expected[4, 1, 4] = 3000.0  # row 2 * 0.5mm = 1mm -> y index 1; column 4 * 1.0mm -> x index 4
    assert np.allclose(vol.array, expected, atol=1e-3)

    meta = json.loads(result.meta_bytes)
    assert meta["spatial"]["original_spacing_mm"]["row"] == 0.5
    assert meta["spatial"]["original_spacing_mm"]["column"] == 1.0
