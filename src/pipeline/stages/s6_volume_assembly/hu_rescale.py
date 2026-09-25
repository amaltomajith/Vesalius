"""Channel: Hounsfield Unit rescale. DICOM.md Part 2: "hu = stored*slope + intercept."

The signed/unsigned trap DICOM.md warns about ("Decoding signed data as unsigned gives
wildly wrong values") is a decode-time concern, not a rescale-math concern -- this module
only ever receives an already-correctly-decoded array from `ds.pixel_array`, which pydicom
decodes according to PixelRepresentation internally. Nothing here reinterprets raw bytes.
"""

from __future__ import annotations

import numpy as np


def to_intensity_float32(stored: np.ndarray, slope: float, intercept: float) -> np.ndarray:
    """DICOM.md Part 2 verbatim. Output dtype is always float32 (volume_contract's
    array.dtype), regardless of the input array's dtype.
    """
    return stored.astype(np.float32) * np.float32(slope) + np.float32(intercept)
