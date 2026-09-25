"""S7's NIfTI read/write against real SimpleITK. The S6 lesson: a sitk write -> read round trip is
blind to a wrong header (ITK's LPS<->RAS flip is symmetric), so every geometry assertion parses
the RAW header bytes."""

from __future__ import annotations

import hashlib
import os
import tempfile

import numpy as np
import pytest
import SimpleITK as sitk  # noqa: N813

from pipeline.stages.s6_volume_assembly.affine import LPS_TO_RAS, parse_nifti_sform
from pipeline.stages.s7_deface import nifti_io
from tests.fixtures.deface_runs import nifti_bytes
from tests.fixtures.head_phantom import ROTATIONS


def _affine(rotation: np.ndarray, origin: tuple[float, float, float]) -> np.ndarray:
    a = np.eye(4)
    a[:3, :3] = rotation
    a[:3, 3] = origin
    return a


def _array(shape: tuple[int, int, int] = (9, 11, 13)) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.uniform(-1000, 1500, size=shape).astype(np.float32)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.mark.parametrize("name", sorted(ROTATIONS))
def test_load_reads_the_array_and_the_raw_sform_for_every_orientation(name: str) -> None:
    affine = _affine(ROTATIONS[name], (10.0, -20.0, 30.0))
    arr = _array()
    loaded = nifti_io.load_volume(nifti_bytes(arr, affine))
    assert np.array_equal(loaded.array, arr)
    assert np.allclose(loaded.sform, affine, atol=1e-3)
    assert len(loaded.header) == 348


def _cmp(source: bytes, out: bytes) -> nifti_io.HeaderComparison:
    return nifti_io.compare_headers(nifti_io.header_bytes(source), nifti_io.header_bytes(out), 1e-3)


@pytest.mark.parametrize("name", sorted(ROTATIONS))
def test_an_identity_rewrite_keeps_the_header_and_the_voxels(name: str) -> None:
    original = nifti_bytes(_array(), _affine(ROTATIONS[name], (1.0, 2.0, 3.0)))
    loaded = nifti_io.load_volume(original)
    out = nifti_io.write_like(loaded.image, loaded.array)
    assert _cmp(original, out).equivalent
    assert np.array_equal(nifti_io.load_volume(out).array, loaded.array)
    if name != "tilted":
        assert out == original  # axis-aligned: byte-identical


def test_an_oblique_volume_may_differ_by_a_float_ulp_in_the_geometry_block_only() -> None:
    """Found empirically: ITK re-derives the quaternion/sform floats from doubles, so a
    gantry-tilted volume can come back one ulp off. An exact-bytes header check would falsely
    quarantine it; the tolerant check must still reject any real difference."""
    original = nifti_bytes(_array(), _affine(ROTATIONS["tilted"], (1.0, 2.0, 3.0)))
    loaded = nifti_io.load_volume(original)
    cmp = _cmp(original, nifti_io.write_like(loaded.image, loaded.array))
    assert cmp.non_geometry_identical and cmp.geometry_close and cmp.equivalent


def test_the_header_comparison_rejects_real_differences() -> None:
    src = nifti_io.header_bytes(
        nifti_bytes(_array(), _affine(ROTATIONS["ras_native"], (1.0, 2.0, 3.0)))
    )
    assert nifti_io.compare_headers(src, src, 1e-3).exact

    datatype = bytearray(src)
    datatype[70] ^= 0xFF  # `datatype` -- outside the geometry block
    cmp = nifti_io.compare_headers(src, bytes(datatype), 1e-3)
    assert not cmp.non_geometry_identical and not cmp.equivalent

    import struct

    moved = bytearray(src)
    (x,) = struct.unpack("<f", src[268:272])  # qoffset_x, shifted by 1 mm
    moved[268:272] = struct.pack("<f", x + 1.0)
    cmp = nifti_io.compare_headers(src, bytes(moved), 1e-3)
    assert cmp.non_geometry_identical and not cmp.geometry_close and not cmp.equivalent

    sform = bytearray(src)
    (t,) = struct.unpack("<f", src[292:296])  # srow_x[3]
    sform[292:296] = struct.pack("<f", t + 1.0)
    assert not nifti_io.compare_headers(src, bytes(sform), 1e-3).equivalent


@pytest.mark.parametrize("name", sorted(ROTATIONS))
def test_editing_the_array_keeps_the_header_and_changes_only_voxels(name: str) -> None:
    original = nifti_bytes(_array(), _affine(ROTATIONS[name], (5.0, 6.0, 7.0)))
    loaded = nifti_io.load_volume(original)
    edited = loaded.array.copy()
    edited[2:5, 3:6, 4:7] = -1000.0
    out = nifti_io.write_like(loaded.image, edited)
    assert _cmp(original, out).equivalent
    assert np.allclose(parse_nifti_sform(nifti_io.header_bytes(out)), loaded.sform, atol=1e-3)
    reread = nifti_io.load_volume(out)
    assert np.array_equal(reread.array, edited)
    assert not np.array_equal(reread.array, loaded.array)


def test_two_writes_of_the_same_array_have_the_same_sha256() -> None:
    loaded = nifti_io.load_volume(
        nifti_bytes(_array(), _affine(ROTATIONS["tilted"], (0.0, 0.0, 0.0)))
    )
    a = nifti_io.write_like(loaded.image, loaded.array)
    b = nifti_io.write_like(loaded.image, loaded.array)
    assert _sha(a) == _sha(b)


def test_the_pre_flip_to_ras_bug_is_visible_in_the_raw_header_but_not_in_a_sitk_round_trip() -> (
    None
):
    """Regression for S6's silent x/y mirror: build the image from the RAS affine as if it were
    LPS. ITK flips again on write, so the header disagrees with the intended affine -- yet a
    sitk round trip reads back the 'same' image."""
    affine = _affine(np.diag([-1.0, -1.0, 1.0]), (50.0, 60.0, -70.0))
    good = nifti_bytes(_array(), affine)

    wrong = sitk.GetImageFromArray(_array())
    wrong.SetDirection(tuple(float(v) for v in affine[:3, :3].flatten()))  # RAS given as LPS
    wrong.SetOrigin(tuple(float(v) for v in affine[:3, 3]))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "w.nii.gz")
        sitk.WriteImage(wrong, path)
        with open(path, "rb") as fh:
            bad = fh.read()
        assert sitk.ReadImage(path).GetOrigin() == wrong.GetOrigin()  # round trip: looks fine

    assert nifti_io.header_bytes(bad) != nifti_io.header_bytes(good)
    assert not np.allclose(parse_nifti_sform(nifti_io.header_bytes(bad)), affine, atol=1e-3)
    assert np.allclose(parse_nifti_sform(nifti_io.header_bytes(good)), affine, atol=1e-3)
    # sanity: the expected wrong header is the LPS-valued geometry in an RAS-labelled file
    assert np.allclose(
        parse_nifti_sform(nifti_io.header_bytes(bad)), LPS_TO_RAS @ affine, atol=1e-3
    )


def test_a_truncated_or_non_gzip_input_is_rejected() -> None:
    with pytest.raises((ValueError, OSError, EOFError)):
        nifti_io.load_volume(b"not a nifti")
    with pytest.raises((ValueError, OSError, EOFError)):
        nifti_io.header_bytes(b"")
