"""The synthetic scans the interface offers: geometry, planted identifiers, and packaging.

The phantoms are left-right symmetric, so an orientation bug (mirrored columns, flipped rows)
would be invisible on them. A deliberately asymmetric recipe is added here so the LPS
conversion is actually tested: a marker on the patient's RIGHT and ANTERIOR side must land in
the image columns/rows the DICOM standard says (columns run toward patient LEFT, rows toward
posterior).
"""

from __future__ import annotations

import io
import zipfile

import numpy as np
import pydicom
import pytest

from pipeline.demo import dicom_series, phantoms
from pipeline.demo.dicom_series import RECIPES, _Recipe, build_demo_series


def _read_all(series: dicom_series.DemoSeries) -> list[pydicom.Dataset]:
    return [pydicom.dcmread(io.BytesIO(b)) for b in series.files.values()]


@pytest.fixture(scope="module")
def head() -> dicom_series.DemoSeries:
    return build_demo_series("head")


@pytest.fixture(scope="module")
def chest() -> dicom_series.DemoSeries:
    return build_demo_series("chest")


def test_slice_counts_and_names(
    head: dicom_series.DemoSeries, chest: dicom_series.DemoSeries
) -> None:
    assert len(head.files) == 106 and len(chest.files) == 34
    assert list(head.files)[:2] == ["IM0001.dcm", "IM0002.dcm"]


def test_one_study_one_series_one_frame_of_reference(head: dicom_series.DemoSeries) -> None:
    ds = _read_all(head)
    for tag in ("StudyInstanceUID", "SeriesInstanceUID", "FrameOfReferenceUID"):
        assert len({str(d[tag].value) for d in ds}) == 1, tag
    assert len({str(d.SOPInstanceUID) for d in ds}) == len(ds)


def test_slices_are_evenly_spaced_and_ordered_inferior_to_superior(
    head: dicom_series.DemoSeries,
) -> None:
    ds = _read_all(head)
    z = np.array([float(d.ImagePositionPatient[2]) for d in ds])
    assert np.allclose(np.diff(z), 2.0) and z[0] == -105.0
    assert [int(d.InstanceNumber) for d in ds] == list(range(1, 107))
    assert all(float(d.SliceThickness) == float(d.SpacingBetweenSlices) == 2.0 for d in ds)


def test_the_tags_the_quality_gate_requires_are_present(head: dicom_series.DemoSeries) -> None:
    d = _read_all(head)[0]
    for tag in (
        "ImagePositionPatient",
        "ImageOrientationPatient",
        "PixelSpacing",
        "RescaleSlope",
        "RescaleIntercept",
        "Rows",
        "Columns",
        "BodyPartExamined",
        "Modality",
    ):
        assert tag in d, tag
    assert d.Modality == "CT" and d.BodyPartExamined == "HEAD" and d.RescaleIntercept == -1024
    assert list(d.ImageOrientationPatient) == [1, 0, 0, 0, 1, 0]
    assert (d.Rows, d.Columns) == (229, 151)


def test_pixels_encode_hounsfield_units_through_the_rescale(head: dicom_series.DemoSeries) -> None:
    mid = _read_all(head)[52]  # z = -1 mm: through the brain
    hu = mid.pixel_array.astype(float) * mid.RescaleSlope + mid.RescaleIntercept
    assert hu.min() == -1000.0 and hu.max() == 1000.0  # air ... skull
    assert 0 < (hu == phantoms.BRAIN).sum() < hu.size


def test_the_face_is_at_the_top_of_the_image_and_lands_where_lps_says(
    head: dicom_series.DemoSeries,
) -> None:
    """Nose: RAS (0, ~110, -10). Anterior = small LPS y = small row; x=0 = the middle column."""
    ds = _read_all(head)
    d = next(x for x in ds if abs(float(x.ImagePositionPatient[2]) - (-11.0)) < 0.1)
    hu = d.pixel_array.astype(float) + d.RescaleIntercept
    y_lps0 = float(d.ImagePositionPatient[1])  # -130
    row = int(round(-110.0 - y_lps0))  # y_LPS = -y_RAS
    assert hu[row, 75] == phantoms.SOFT  # the nose, at the centre column
    assert hu[:, 75][:5].max() == phantoms.AIR  # nothing in front of the nose
    assert (hu[: row - 5] == phantoms.AIR).all() or hu[: row - 5].max() <= phantoms.SOFT


def _asymmetric(x: np.ndarray, y: np.ndarray, z: np.ndarray) -> np.ndarray:
    out = np.full(np.broadcast(x, y, z).shape, -1000.0, dtype=np.float32)
    out[(x > 10) & (y > 10) & (np.abs(z) < 4)] = 1000.0  # patient's RIGHT and ANTERIOR
    return out


def test_a_marker_on_the_patients_right_and_front_lands_in_the_dicom_standard_corner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        RECIPES,
        "asym",
        _Recipe(_asymmetric, ((-30.0, 30.0), (-30.0, 30.0), (-6.0, 6.0)), "HEAD", "X", 2.0),
    )
    ds = _read_all(build_demo_series("asym"))
    d = ds[len(ds) // 2]
    assert float(d.ImagePositionPatient[0]) == -30.0 and float(d.ImagePositionPatient[1]) == -30.0
    ys, xs = np.nonzero(d.pixel_array.astype(float) + d.RescaleIntercept > 500)
    # columns run toward patient LEFT: patient RIGHT (x_RAS>0 -> x_LPS<0) is the image's low columns
    assert xs.max() < 30, "right-side marker must be in the low-column half"
    # rows run toward posterior: ANTERIOR (y_RAS>0 -> y_LPS<0) is the TOP of the image
    assert ys.max() < 30, "anterior marker must be in the low-row half"


def test_planted_identifiers_are_present_fake_and_removable(head: dicom_series.DemoSeries) -> None:
    d = _read_all(head)[0]
    assert d.PatientName == "DEMO^SYNTHETIC^PATIENT" and d.PatientID == "DEMO-000001"
    assert d.PatientBirthDate == "19700101" and d.InstitutionName == "DEMO GENERAL HOSPITAL"
    assert "DEMO^PATIENT" in d.StudyDescription and d.PatientComments.startswith("contact 555")
    assert d[(0x0009, 0x0010)].value == "DEMO VENDOR BLOCK"
    assert len(head.planted) == 8 and {p.channel for p in head.planted} == {"tag", "private"}
    for p in head.planted:
        assert "DEMO" in p.value or p.value == "19700101", p  # unmistakably fake


def test_planting_can_be_switched_off() -> None:
    clean = build_demo_series("chest", plant_identifiers=False)
    d = _read_all(clean)[0]
    assert clean.planted == () and "DEMO" not in str(d.PatientName) and "PatientBirthDate" not in d
    assert (0x0009, 0x0010) not in d


def test_zip_packaging(head: dicom_series.DemoSeries) -> None:
    with zipfile.ZipFile(io.BytesIO(head.as_zip())) as zf:
        assert len(zf.namelist()) == 106 and zf.testzip() is None


def test_the_chest_is_a_chest_and_unknown_kinds_are_refused(chest: dicom_series.DemoSeries) -> None:
    assert chest.body_part == "CHEST" and _read_all(chest)[0].BodyPartExamined == "CHEST"
    with pytest.raises(ValueError):
        build_demo_series("abdomen")


def test_the_phantoms_reproduce_the_tested_head_geometry() -> None:
    """The head is a port of tests/fixtures/head_phantom.py: same landmarks at the same places."""
    x = np.array([0.0, 0.0, 0.0, 0.0])
    y = np.array([123.0 - 1.0, 62.0, -35.0, 0.0])
    z = np.array([-10.0, -55.0, -88.0, 0.0])  # (0, 62, -55) is bone, clear of the sinus
    hu = phantoms.head_hu(x, y, z)
    assert hu[0] == phantoms.SOFT  # just inside the nose tip
    assert hu[1] == phantoms.BONE  # facial bone block
    assert hu[2] == phantoms.BONE  # skull base
    assert hu[3] == phantoms.BRAIN
    assert (
        phantoms.head_hu(np.array([0.0]), np.array([66.0]), np.array([-30.0]))[0] == phantoms.AIR
    )  # sinus
