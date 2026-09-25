"""Synthesizes small, valid, in-memory DICOM datasets and byte streams for S1/S2 tests.

pydicom's bundled test data (`pydicom.data.get_testdata_file()`) is mostly single-instance
and used for malformed/unusual-file tests (refs/TESTING.md Part 3). Series-grouping tests
need a real multi-instance series, which pydicom doesn't ship -- this factory builds one,
in the same spirit as TESTING.md's "generate degraded fixtures programmatically from clean
ones" principle.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.tag import Tag
from pydicom.uid import ExplicitVRLittleEndian, generate_uid


def build_dataset(
    *,
    study_uid: str | None = None,
    series_uid: str | None = None,
    sop_uid: str | None = None,
    patient_id: str | None = "PAT001",
    modality: str = "CT",
    body_part: str | None = "CHEST",
    rows: int = 4,
    columns: int = 4,
    instance_number: int = 1,
    image_position: tuple[float, float, float] = (0.0, 0.0, 0.0),
    include_optional: bool = True,
    include_frame_of_reference: bool = True,
    orientation: Sequence[float] | None = None,
    omit_tags: Sequence[str] = (),
    study_description: str | None = None,
    pixel_data: Literal["none", "valid", "corrupt"] = "none",
    extra_tags: dict[str, str] | None = None,
    private_tags: dict[tuple[int, int], tuple[str, str]] | None = None,
    burned_text: str | None = None,
    pixel_spacing: tuple[float, float] | None = None,
    uniform_raw: int | None = None,
    marker: tuple[int, int, int] | None = None,
) -> Dataset:
    study_uid = study_uid or generate_uid()
    series_uid = series_uid or generate_uid()
    sop_uid = sop_uid or generate_uid()

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = generate_uid()
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    file_meta.ImplementationClassUID = generate_uid()

    ds = Dataset()
    ds.file_meta = file_meta

    ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    if patient_id is not None:
        ds.PatientID = patient_id
    ds.PatientName = "Test^Patient"
    ds.Modality = modality
    ds.InstanceNumber = instance_number
    ds.Rows = rows
    ds.Columns = columns

    if body_part is not None:
        ds.BodyPartExamined = body_part

    if study_description is not None:
        ds.StudyDescription = study_description

    if include_optional:
        ds.PixelSpacing = list(pixel_spacing) if pixel_spacing is not None else [0.5, 0.5]
        ds.ImageOrientationPatient = (
            list(orientation)
            if orientation is not None
            else [
                1,
                0,
                0,
                0,
                1,
                0,
            ]
        )
        ds.ImagePositionPatient = list(image_position)
        ds.SliceThickness = 1.0
        ds.SpacingBetweenSlices = 1.0
        ds.RescaleSlope = 1.0
        ds.RescaleIntercept = -1024.0
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.BurnedInAnnotation = "NO"

    if include_frame_of_reference:
        ds.FrameOfReferenceUID = generate_uid()

    if pixel_data != "none":
        ds.SamplesPerPixel = 1
        bytes_per_sample = ds.BitsAllocated // 8
        expected_bytes = rows * columns * bytes_per_sample
        if pixel_data == "valid":
            if burned_text is not None:
                arr = _render_burned_text(rows, columns, burned_text)
            elif uniform_raw is not None:
                arr = np.full((rows, columns), uniform_raw, dtype=np.int16)
            else:
                arr = (np.arange(rows * columns, dtype=np.int16) % 100).astype(np.int16)
            if marker is not None:
                marker_row, marker_col, marker_value = marker
                arr = np.array(arr, dtype=np.int16).reshape(rows, columns)
                arr[marker_row, marker_col] = marker_value
            ds.PixelData = arr.tobytes()
        else:  # "corrupt" -- buffer too short, pydicom/numpy raises on pixel_array reshape
            ds.PixelData = b"\x00" * (expected_bytes // 2)
    elif burned_text is not None:
        raise ValueError("burned_text requires pixel_data='valid'")

    if extra_tags:
        for name, value in extra_tags.items():
            setattr(ds, name, value)

    if private_tags:
        for (group, element), (vr, value) in private_tags.items():
            ds.add_new(Tag(group, element), vr, value)

    for tag in omit_tags:
        if hasattr(ds, tag):
            delattr(ds, tag)

    return ds


_DEJAVU_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _load_burn_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # A real scalable TrueType font renders with clean anti-aliased edges that OCR handles
    # far better than PIL's tiny built-in bitmap font (~11px, fixed size, blocky even after
    # upscaling) -- confirmed via a real tesseract run: the bitmap-font approach measured
    # only ~29% confidence on burned text, below any sane detection threshold. Falls back
    # to the bitmap font only if the TTF genuinely isn't installed (see docker/api/
    # Dockerfile's fonts-dejavu-core) so this doesn't hard-crash outside that image.
    try:
        return ImageFont.truetype(_DEJAVU_FONT_PATH, size=size)
    except OSError:
        return ImageFont.load_default()


def _render_burned_text(rows: int, columns: int, text: str) -> np.ndarray:
    """Draws `text` near the top-left of a (rows, columns) canvas -- DICOM.md: "Text in
    the top ~15% of the frame is the common case (vendor overlay banner)." Callers should
    pass reasonably large rows/columns (e.g. 200x200); a font sized relative to the
    canvas height keeps the text legible to OCR at that resolution.
    """
    image = Image.new("L", (columns, rows), color=20)
    draw = ImageDraw.Draw(image)
    font = _load_burn_font(size=max(12, rows // 8))
    draw.text((max(2, columns // 40), max(2, rows // 40)), text, fill=230, font=font)
    return np.array(image, dtype=np.int16)


def dataset_to_bytes(ds: Dataset) -> bytes:
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def build_dicom_bytes(**kwargs: object) -> bytes:
    return dataset_to_bytes(build_dataset(**kwargs))  # type: ignore[arg-type]


def build_series(
    count: int = 3,
    *,
    overrides: dict[int, dict[str, object]] | None = None,
    **kwargs: object,
) -> list[bytes]:
    """A small clean series: shared StudyInstanceUID/SeriesInstanceUID, varied
    SOPInstanceUID/InstanceNumber/ImagePositionPatient (STAGES.md S2 grouping keys).

    `overrides={2: {"orientation": [...]}}` injects per-instance-index kwargs on top of
    the shared `**kwargs` -- one mechanism for every single-defect fixture (nonuniform
    spacing, mixed orientation, missing geometry on one instance, dimension mismatch),
    matching TESTING.md's "generate degraded fixtures programmatically from clean ones."
    """
    study_uid = kwargs.pop("study_uid", None) or generate_uid()
    series_uid = kwargs.pop("series_uid", None) or generate_uid()
    overrides = overrides or {}
    out = []
    for i in range(count):
        instance_kwargs: dict[str, object] = {
            "study_uid": study_uid,
            "series_uid": series_uid,
            "instance_number": i + 1,
            "image_position": (0.0, 0.0, float(i) * 2.0),
            **kwargs,
            **overrides.get(i, {}),
        }
        ds = build_dataset(**instance_kwargs)  # type: ignore[arg-type]
        out.append(dataset_to_bytes(ds))
    return out


def build_clean_ct_series(count: int = 3, **kwargs: object) -> list[bytes]:
    """Convenience wrapper: CT, valid synthetic pixel data. Every S3 happy-path and
    single-defect fixture starts here, then injects exactly one defect via `overrides`.
    """
    return build_series(count=count, modality="CT", pixel_data="valid", **kwargs)


# The factory's fixed rescale is slope=1.0, intercept=-1024.0 (see build_dataset), so
# HU = raw*1.0 - 1024  =>  raw = HU + 1024:
#   background HU 0.0    -> raw 1024
#   marker     HU 3000.0 -> raw 4024      (both fit comfortably in signed int16)
_FIXTURE_RESCALE_INTERCEPT = -1024.0


def build_asymmetric_marker_series(
    *,
    rows: int = 8,
    columns: int = 7,
    count: int = 5,
    marker_slice_index: int = 4,
    marker_row: int = 1,
    marker_col: int = 5,
    marker_hu: float = 3000.0,
    background_hu: float = 0.0,
    modality: str = "CT",
    body_part: str = "CHEST",
    pixel_spacing: tuple[float, float] = (1.0, 1.0),
) -> list[bytes]:
    """The mirror/axis-swap detector fixture (S6 plan §10).

    rows != columns != count by construction, so a transpose/dimension-swap bug is caught as
    well as a pure mirror. Geometry is axis-aligned (IOP [1,0,0,0,1,0]), 1.0mm in-plane and
    1.0mm between slices, so the assembly's 1.0mm isotropic target causes no resampling and
    the marker's expected output index is exact integer arithmetic, not interpolated.

    Slice k sits at ImagePositionPatient z = k (position strictly increasing with k), but
    InstanceNumber is assigned in REVERSE (count - k): the physically-last slice has
    InstanceNumber 1. So a regression to sorting by InstanceNumber flips the z axis and moves
    the marker to output index 0 instead of `marker_slice_index`. The marker is a single
    pixel on one slice; every other voxel decodes to `background_hu`.
    """
    background_raw = round(background_hu - _FIXTURE_RESCALE_INTERCEPT)
    marker_raw = round(marker_hu - _FIXTURE_RESCALE_INTERCEPT)
    overrides: dict[int, dict[str, object]] = {}
    for k in range(count):
        entry: dict[str, object] = {
            "instance_number": count - k,
            "image_position": (0.0, 0.0, float(k)),
        }
        if k == marker_slice_index:
            entry["marker"] = (marker_row, marker_col, marker_raw)
        overrides[k] = entry
    return build_series(
        count=count,
        overrides=overrides,
        modality=modality,
        body_part=body_part,
        rows=rows,
        columns=columns,
        pixel_data="valid",
        pixel_spacing=pixel_spacing,
        uniform_raw=background_raw,
    )
