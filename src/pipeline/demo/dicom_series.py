"""A synthetic HU volume -> a valid multi-slice DICOM series (as a zip), with clearly fake
identifiers planted so the de-identification stages have something real to remove.

Geometry is written the way scanners write it: axial slices, DICOM patient coordinates (LPS),
ImageOrientationPatient [1,0,0,0,1,0] (rows run toward the patient's left, columns toward the
patient's posterior), ImagePositionPatient the LPS position of each slice's first pixel, slices
listed inferior to superior. The phantom lives in RAS, so x and y are negated on the way in -- the
very conversion the pipeline's S6 has to undo, which makes this a genuine end-to-end geometry test.

Pixels are int16 with RescaleSlope 1 / RescaleIntercept -1024 (raw = HU + 1024), matching S3's
"CT must carry rescale tags" rule. Every planted identifier is unmistakably fake (`DEMO...`).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import UID, ExplicitVRLittleEndian, generate_uid

from pipeline.demo import phantoms

CT_IMAGE_STORAGE = UID("1.2.840.10008.5.1.4.1.1.2")
RESCALE_INTERCEPT = -1024.0
PIXEL_SPACING_MM = 1.0


@dataclass(frozen=True)
class PlantedIdentifier:
    label: str  # plain-language name, e.g. "Patient name"
    value: str
    channel: str  # "tag" | "private"


@dataclass(frozen=True)
class DemoSeries:
    """One generated series: file names -> DICOM bytes, plus what was planted in it."""

    kind: str
    body_part: str
    files: dict[str, bytes]
    planted: tuple[PlantedIdentifier, ...]

    def as_zip(self) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in self.files.items():
                zf.writestr(name, data)
        return buf.getvalue()


@dataclass(frozen=True)
class _Recipe:
    hu: Callable[[np.ndarray, np.ndarray, np.ndarray], np.ndarray]
    extent: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]
    body_part: str
    description: str
    slice_spacing_mm: float


RECIPES: dict[str, _Recipe] = {
    "head": _Recipe(phantoms.head_hu, phantoms.HEAD_EXTENT, "HEAD", "CT HEAD", 2.0),
    "chest": _Recipe(phantoms.chest_hu, phantoms.CHEST_EXTENT, "CHEST", "CT CHEST", 3.0),
}

_PLANTED = (
    PlantedIdentifier("Patient name", "DEMO^SYNTHETIC^PATIENT", "tag"),
    PlantedIdentifier("Patient ID", "DEMO-000001", "tag"),
    PlantedIdentifier("Date of birth", "19700101", "tag"),
    PlantedIdentifier("Hospital name", "DEMO GENERAL HOSPITAL", "tag"),
    PlantedIdentifier("Referring doctor", "DEMO^DOCTOR", "tag"),
    PlantedIdentifier("Name inside the study description", "DEMO^PATIENT", "tag"),
    PlantedIdentifier("Comment with a phone number", "contact 555-0142 DEMO", "tag"),
    PlantedIdentifier("Vendor-private block", "DEMO VENDOR BLOCK", "private"),
)


def _dataset(
    *,
    recipe: _Recipe,
    pixels: np.ndarray,
    z_mm: float,
    x_lps0: float,
    y_lps0: float,
    instance_number: int,
    study_uid: str,
    series_uid: str,
    frame_uid: str,
    plant: bool,
) -> Dataset:
    sop_uid = generate_uid()
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CT_IMAGE_STORAGE
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = CT_IMAGE_STORAGE
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.FrameOfReferenceUID = frame_uid
    ds.Modality = "CT"
    ds.BodyPartExamined = recipe.body_part
    ds.SeriesDescription = recipe.description
    ds.InstanceNumber = instance_number
    ds.PatientSex = "O"
    ds.PatientID = "DEMO-000001" if plant else "ANON-0001"
    ds.PatientName = "DEMO^SYNTHETIC^PATIENT" if plant else "ANON"

    rows, columns = pixels.shape
    ds.Rows = rows
    ds.Columns = columns
    ds.PixelSpacing = [PIXEL_SPACING_MM, PIXEL_SPACING_MM]
    ds.SliceThickness = recipe.slice_spacing_mm
    ds.SpacingBetweenSlices = recipe.slice_spacing_mm
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.ImagePositionPatient = [x_lps0, y_lps0, z_mm]  # z is superior in both LPS and RAS
    ds.RescaleSlope = 1.0
    ds.RescaleIntercept = RESCALE_INTERCEPT
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SamplesPerPixel = 1
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.BurnedInAnnotation = "NO"
    ds.PixelData = pixels.astype("<i2").tobytes()

    if plant:
        ds.PatientBirthDate = "19700101"
        ds.StudyDate = "20200315"
        ds.InstitutionName = "DEMO GENERAL HOSPITAL"
        ds.ReferringPhysicianName = "DEMO^DOCTOR"
        ds.StudyDescription = f"{recipe.description} - DEMO^PATIENT"  # a name inside free text
        ds.PatientComments = "contact 555-0142 DEMO"
        ds.add_new((0x0009, 0x0010), "LO", "DEMO VENDOR BLOCK")
    else:
        ds.StudyDescription = recipe.description
    return ds


def _to_bytes(ds: Dataset) -> bytes:
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


def build_demo_series(kind: str, *, plant_identifiers: bool = True) -> DemoSeries:
    if kind not in RECIPES:
        raise ValueError(f"unknown demo scan {kind!r} (known: {sorted(RECIPES)})")
    recipe = RECIPES[kind]
    (x0, x1), (y0, y1), (z0, z1) = recipe.extent

    cols = int(round((x1 - x0) / PIXEL_SPACING_MM)) + 1
    rows = int(round((y1 - y0) / PIXEL_SPACING_MM)) + 1
    n_slices = int(np.floor((z1 - z0) / recipe.slice_spacing_mm)) + 1

    # LPS position of the first pixel: leftmost column (-x_RAS max) and the most anterior row
    # (LPS y is posterior, so the anterior-most row has the smallest y_LPS = -y_RAS max).
    x_lps0, y_lps0 = -x1, -y1
    c: np.ndarray = np.arange(cols, dtype=np.float32)[None, :]
    r: np.ndarray = np.arange(rows, dtype=np.float32)[:, None]
    x_ras = -(x_lps0 + c * PIXEL_SPACING_MM)
    y_ras = -(y_lps0 + r * PIXEL_SPACING_MM)

    study_uid, series_uid, frame_uid = generate_uid(), generate_uid(), generate_uid()
    files: dict[str, bytes] = {}
    for k in range(n_slices):
        z = z0 + k * recipe.slice_spacing_mm
        hu = recipe.hu(x_ras, y_ras, np.asarray(z, dtype=np.float32))
        raw = np.clip(np.rint(hu - RESCALE_INTERCEPT), 0, 32767).astype(np.int16)
        ds = _dataset(
            recipe=recipe,
            pixels=raw,
            z_mm=float(z),
            x_lps0=float(x_lps0),
            y_lps0=float(y_lps0),
            instance_number=k + 1,
            study_uid=study_uid,
            series_uid=series_uid,
            frame_uid=frame_uid,
            plant=plant_identifiers,
        )
        files[f"IM{k + 1:04d}.dcm"] = _to_bytes(ds)
    return DemoSeries(
        kind=kind,
        body_part=recipe.body_part,
        files=files,
        planted=_PLANTED if plant_identifiers else (),
    )
