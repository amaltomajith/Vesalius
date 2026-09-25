"""Isotropic resampling. DICOM.md Part 2: "Use SimpleITK with linear interpolation for
intensity volumes... do not hand-roll it." Resampling only; image construction and the
NIfTI write live in service.py (which also uses SimpleITK).
"""

from __future__ import annotations

import SimpleITK as sitk  # noqa: N813 -- universal convention for this library


def resample_isotropic(
    image: sitk.Image, target_spacing_mm: float, default_pixel_value: float
) -> sitk.Image:
    original_spacing = image.GetSpacing()
    original_size = image.GetSize()
    new_size = [
        max(1, round(osz * ospc / target_spacing_mm))
        for osz, ospc in zip(original_size, original_spacing, strict=True)
    ]
    resampler = sitk.ResampleImageFilter()
    resampler.SetOutputSpacing((target_spacing_mm,) * 3)
    resampler.SetSize(new_size)
    resampler.SetOutputOrigin(image.GetOrigin())
    resampler.SetOutputDirection(image.GetDirection())
    resampler.SetInterpolator(sitk.sitkLinear)
    resampler.SetDefaultPixelValue(default_pixel_value)
    return resampler.Execute(image)
