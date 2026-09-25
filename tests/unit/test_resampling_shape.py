"""Isotropic resampling geometry (DICOM.md Part 2). Pure SimpleITK, no DICOM/MinIO/DB."""

from __future__ import annotations

import numpy as np
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library

from pipeline.stages.s6_volume_assembly.resampling import resample_isotropic


def _image(size_xyz: tuple[int, int, int], spacing: tuple[float, float, float]) -> sitk.Image:
    x, y, z = size_xyz
    image = sitk.GetImageFromArray(
        np.arange(x * y * z, dtype=np.float32).reshape(z, y, x)  # numpy is (z, y, x)
    )
    image.SetSpacing(spacing)
    image.SetOrigin((5.0, -3.0, 12.0))
    return image


def test_dicom_md_example_anisotropic_volume_becomes_isotropic() -> None:
    # DICOM.md's own example: 0.7 x 0.7 x 3.0 mm voxels.
    out = resample_isotropic(_image((10, 10, 4), (0.7, 0.7, 3.0)), 1.0, default_pixel_value=-1024.0)
    assert out.GetSize() == (7, 7, 12)  # round(10*0.7), round(10*0.7), round(4*3.0)
    assert out.GetSpacing() == (1.0, 1.0, 1.0)
    assert sitk.GetArrayFromImage(out).shape == (12, 7, 7)  # (z, y, x)


def test_axes_are_scaled_independently_not_by_a_common_factor() -> None:
    out = resample_isotropic(_image((8, 6, 5), (0.5, 1.0, 2.0)), 1.0, default_pixel_value=0.0)
    assert out.GetSize() == (4, 6, 10)


def test_resampling_is_a_noop_when_already_at_the_target_spacing() -> None:
    source = _image((6, 5, 4), (1.0, 1.0, 1.0))
    out = resample_isotropic(source, 1.0, default_pixel_value=-1024.0)
    assert out.GetSize() == source.GetSize()
    assert np.allclose(sitk.GetArrayFromImage(out), sitk.GetArrayFromImage(source))


def test_origin_and_direction_are_preserved() -> None:
    source = _image((6, 5, 4), (0.5, 0.5, 2.0))
    source.SetDirection((0, 1, 0, -1, 0, 0, 0, 0, 1))
    out = resample_isotropic(source, 1.0, default_pixel_value=0.0)
    assert out.GetOrigin() == source.GetOrigin()
    assert out.GetDirection() == source.GetDirection()


def test_output_stays_float32() -> None:
    out = resample_isotropic(_image((6, 5, 4), (0.5, 0.5, 2.0)), 1.0, default_pixel_value=0.0)
    assert sitk.GetArrayFromImage(out).dtype == np.float32
