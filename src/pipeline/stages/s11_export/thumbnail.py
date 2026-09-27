"""S11's thumbnail: the middle axial slice of the GATED intensity volume, percentile-clipped and
stretched to 8-bit, resized to a fixed size, PNG-encoded via `pillow` (already a dependency -- no
3-D renderer added; see the plan's own note on why: no renderer is in the approved stack, and a
renderer would bring GL system libraries back into the image).

`render_thumbnail_png` takes raw NIfTI BYTES only -- never a `series_id` or DB `Session` -- so it
is structurally unable to read silver itself; the caller (`service.py`) is responsible for
sourcing those bytes from `GatedVolume.volume_bytes` and nowhere else. A static test
(`test_s11_service_stubs.py`) asserts this module's only public function has no such parameter.

Returns the pre-encode 8-bit array alongside the PNG bytes so the caller can do a real
write-path check (decode the PNG, compare pixel-for-pixel against the array actually encoded --
PNG's grayscale encoding is lossless, so this should be exact, same "verify what was actually
persisted" discipline as every other stage's serialization round trip).
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image

from pipeline.stages.s7_deface import nifti_io as vol_io
from pipeline.stages.s11_export import params


@dataclass(frozen=True)
class ThumbnailResult:
    png_bytes: bytes
    array_uint8: np.ndarray  # the exact 8-bit pixel grid encoded into png_bytes


def render_thumbnail(volume_nii_gz_bytes: bytes) -> ThumbnailResult:
    loaded = vol_io.load_volume(volume_nii_gz_bytes)
    array = loaded.array  # (z, y, x) float32
    mid_z = array.shape[0] // 2
    axial = array[mid_z]

    lo, hi = np.percentile(
        axial, [params.THUMBNAIL_LOW_PERCENTILE, params.THUMBNAIL_HIGH_PERCENTILE]
    )
    if hi <= lo:
        stretched = np.zeros_like(axial, dtype=np.uint8)
    else:
        clipped = np.clip(axial, lo, hi)
        stretched = (((clipped - lo) / (hi - lo)) * 255.0).astype(np.uint8)

    image = Image.fromarray(stretched, mode="L").resize(
        params.THUMBNAIL_SIZE_PX, Image.Resampling.LANCZOS
    )
    array_uint8 = np.asarray(image, dtype=np.uint8)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return ThumbnailResult(png_bytes=buf.getvalue(), array_uint8=array_uint8)
