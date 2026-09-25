from __future__ import annotations

import io

import pydicom

from pipeline.stages.s2_parse_index.tags import extract_tags
from tests.fixtures.dicom_factory import build_dataset, dataset_to_bytes


def test_missing_optional_tags_recorded_as_none_not_raised() -> None:
    ds = build_dataset(include_optional=False, include_frame_of_reference=False)
    data = dataset_to_bytes(ds)
    reread = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)

    tags = extract_tags(reread)

    assert tags["PixelSpacing"] is None
    assert tags["ImageOrientationPatient"] is None
    assert tags["ImagePositionPatient"] is None
    assert tags["SpacingBetweenSlices"] is None
    assert tags["RescaleSlope"] is None
    assert tags["RescaleIntercept"] is None
    assert tags["BurnedInAnnotation"] is None
    assert tags["FrameOfReferenceUID"] is None
    # required-for-grouping tags are still present
    assert tags["StudyInstanceUID"] is not None
    assert tags["SeriesInstanceUID"] is not None
    assert tags["SOPInstanceUID"] is not None


def test_present_optional_tags_are_captured() -> None:
    ds = build_dataset(include_optional=True)
    data = dataset_to_bytes(ds)
    reread = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)

    tags = extract_tags(reread)

    assert tags["PixelSpacing"] == [0.5, 0.5]
    assert tags["RescaleSlope"] is not None
    assert float(tags["RescaleSlope"]) == 1.0
    assert tags["BurnedInAnnotation"] == "NO"
