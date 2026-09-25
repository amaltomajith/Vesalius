"""HU rescale and the signed/unsigned trap (DICOM.md Part 2: "Decoding signed data as
unsigned gives wildly wrong values"). TESTING.md Part 4 names this file.
"""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from pipeline.common.config import Settings
from pipeline.stages.s6_volume_assembly.hu_rescale import to_intensity_float32
from pipeline.stages.s6_volume_assembly.service import _assemble
from tests.fixtures.dicom_factory import build_series
from tests.fixtures.volume_asserts import make_in_memory_series, read_volume


def test_typical_ct_rescale() -> None:
    stored = np.array([0, 1024, 2048, 4095], dtype=np.int16)
    hu = to_intensity_float32(stored, slope=1.0, intercept=-1024.0)
    assert np.allclose(hu, [-1024.0, 0.0, 1024.0, 3071.0])


def test_slope_other_than_one_is_applied() -> None:
    hu = to_intensity_float32(np.array([0, 100, 200], dtype=np.int16), slope=2.0, intercept=-1000.0)
    assert np.allclose(hu, [-1000.0, -800.0, -600.0])


def test_identity_default_is_a_noop_for_modalities_without_rescale_tags() -> None:
    stored = np.array([[3, 7], [11, 13]], dtype=np.int16)
    assert np.array_equal(to_intensity_float32(stored, 1.0, 0.0), stored.astype(np.float32))


@pytest.mark.parametrize("dtype", [np.int16, np.uint16, np.uint8, np.int32])
def test_output_dtype_is_always_float32(dtype: type) -> None:
    assert to_intensity_float32(np.array([1, 2, 3], dtype=dtype), 1.0, 0.0).dtype == np.float32


def test_signed_negative_stored_values_give_small_negative_hu_not_a_huge_positive() -> None:
    signed = np.array([-1000, -1, 0], dtype=np.int16)  # legitimate two's-complement negatives
    good = to_intensity_float32(signed, slope=1.0, intercept=-1024.0)
    assert np.allclose(good, [-2024.0, -1025.0, -1024.0])

    # The same bytes reinterpreted as unsigned -- the bug DICOM.md warns about -- is nowhere
    # near: 65536 - 1000 = 64536 stored counts.
    misdecoded = to_intensity_float32(signed.view(np.uint16), slope=1.0, intercept=-1024.0)
    assert misdecoded[0] == pytest.approx(64536.0 - 1024.0)
    assert not np.allclose(misdecoded, good)


def test_real_dicom_with_negative_signed_pixels_survives_the_full_assembly_decode() -> None:
    """The unit above pins the math; this pins the DECODE that feeds it, through the real
    `_assemble` on real DICOM bytes (PixelRepresentation=1, stored value -1000). A regression
    that decoded pixel data as unsigned would yield ~63512 here instead of -2024.
    """
    blobs = build_series(
        count=3,
        overrides={k: {"image_position": (0.0, 0.0, float(k))} for k in range(3)},
        rows=4,
        columns=4,
        pixel_data="valid",
        pixel_spacing=(1.0, 1.0),
        uniform_raw=-1000,
        modality="CT",
    )
    mem = make_in_memory_series(blobs)
    result = _assemble(mem.client, Settings(), mem.series, mem.artifacts_by_instance, uuid4())
    array = read_volume(result.volume_bytes).array
    assert array.shape == (3, 4, 4)
    assert np.allclose(array, -2024.0, atol=1e-3)
