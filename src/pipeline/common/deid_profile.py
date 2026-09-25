"""The de-identification tag profile: the single source of truth for which DICOM tags
must be removed/replaced/remapped, which must be kept, and the (empty) private-tag
whitelist. Transcribed 1:1 from refs/DICOM.md Part 1's "Must remove or replace" table
(PS3.15 Annex E Basic Application Level Confidentiality Profile).

Read by BOTH s4_deidentify (to apply) and s5_verify (to independently check) -- sharing
this DATA is fine and necessary (there must be exactly one canonical list, or the two
stages could silently drift and both be "consistently wrong" together). What must stay
separate is the CODE that walks/applies/checks it -- see s4_deidentify/rules.py vs
s5_verify/rescan.py, which are independently written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Action = Literal["X", "Z", "D", "U", "K"]


@dataclass(frozen=True)
class TagRule:
    tag: tuple[int, int]
    name: str
    action: Action


# DICOM.md Part 1's "Must remove or replace" table, verbatim, all 35 rows.
TAG_PROFILE: tuple[TagRule, ...] = (
    TagRule((0x0008, 0x0014), "InstanceCreatorUID", "U"),
    TagRule((0x0008, 0x0018), "SOPInstanceUID", "U"),
    TagRule((0x0008, 0x0050), "AccessionNumber", "Z"),
    TagRule((0x0008, 0x0080), "InstitutionName", "X"),
    TagRule((0x0008, 0x0081), "InstitutionAddress", "X"),
    TagRule((0x0008, 0x0090), "ReferringPhysicianName", "Z"),
    TagRule((0x0008, 0x0092), "ReferringPhysicianAddress", "X"),
    TagRule((0x0008, 0x0094), "ReferringPhysicianTelephoneNumbers", "X"),
    TagRule((0x0008, 0x1010), "StationName", "X"),
    TagRule((0x0008, 0x1030), "StudyDescription", "X"),
    TagRule((0x0008, 0x103E), "SeriesDescription", "X"),
    TagRule((0x0008, 0x1040), "InstitutionalDepartmentName", "X"),
    TagRule((0x0008, 0x1048), "PhysiciansOfRecord", "X"),
    TagRule((0x0008, 0x1050), "PerformingPhysicianName", "X"),
    TagRule((0x0008, 0x1060), "NameOfPhysiciansReadingStudy", "X"),
    TagRule((0x0008, 0x1070), "OperatorsName", "X"),
    TagRule((0x0008, 0x1080), "AdmittingDiagnosesDescription", "X"),
    TagRule((0x0008, 0x1155), "ReferencedSOPInstanceUID", "U"),
    TagRule((0x0010, 0x0010), "PatientName", "Z"),
    TagRule((0x0010, 0x0020), "PatientID", "Z"),
    TagRule((0x0010, 0x0030), "PatientBirthDate", "Z"),
    TagRule((0x0010, 0x0032), "PatientBirthTime", "X"),
    TagRule((0x0010, 0x1000), "OtherPatientIDs", "X"),
    TagRule((0x0010, 0x1001), "OtherPatientNames", "X"),
    # DICOM.md offers "X (or bucket to 5-year bands)" -- plain X chosen, banding deferred
    # (adds a config surface nothing else needs yet; no ref requests it).
    TagRule((0x0010, 0x1010), "PatientAge", "X"),
    TagRule((0x0010, 0x1020), "PatientSize", "X"),
    TagRule((0x0010, 0x1030), "PatientWeight", "X"),
    TagRule((0x0010, 0x1090), "MedicalRecordLocator", "X"),
    TagRule((0x0010, 0x2160), "EthnicGroup", "X"),
    TagRule((0x0010, 0x2180), "Occupation", "X"),
    TagRule((0x0010, 0x21B0), "AdditionalPatientHistory", "X"),
    TagRule((0x0010, 0x4000), "PatientComments", "X"),
    TagRule((0x0018, 0x1000), "DeviceSerialNumber", "X"),
    TagRule((0x0018, 0x1030), "ProtocolName", "X"),
    TagRule((0x0020, 0x000D), "StudyInstanceUID", "U"),
    TagRule((0x0020, 0x000E), "SeriesInstanceUID", "U"),
    TagRule((0x0020, 0x0010), "StudyID", "Z"),
    TagRule((0x0020, 0x0052), "FrameOfReferenceUID", "U"),
    TagRule((0x0020, 0x4000), "ImageComments", "X"),
    TagRule((0x0032, 0x1032), "RequestingPhysician", "X"),
    TagRule((0x0038, 0x0300), "CurrentPatientLocation", "X"),
    TagRule((0x0040, 0x0275), "RequestAttributesSequence", "X"),
    TagRule((0x0040, 0xA730), "ContentSequence", "X"),
)

TAG_PROFILE_BY_NAME: dict[str, TagRule] = {rule.name: rule for rule in TAG_PROFILE}
TAG_PROFILE_BY_TAG: dict[tuple[int, int], TagRule] = {rule.tag: rule for rule in TAG_PROFILE}

# DICOM.md: "Apply [UID remap] to every UID-valued tag, including references inside
# sequences." Read completely literally this would also catch SOPClassUID/
# TransferSyntaxUID/coding-scheme UIDs -- format/class identifiers, not patient
# identifiers, and remapping them would produce an invalid or undecodable file. This
# allowlist is the resolution: only instance-identifying UIDs are remapped.
UID_REMAP_TAG_NAMES: frozenset[str] = frozenset(
    {
        "InstanceCreatorUID",
        "SOPInstanceUID",
        "ReferencedSOPInstanceUID",
        "StudyInstanceUID",
        "SeriesInstanceUID",
        "FrameOfReferenceUID",
    }
)

# Deliberately excluded from UID_REMAP_TAG_NAMES -- these are format/class identifiers,
# not patient/instance identifiers, and must survive unchanged for the file to remain a
# valid, decodable DICOM object.
UID_REMAP_EXCLUDED_NAMES: frozenset[str] = frozenset(
    {
        "SOPClassUID",
        "ReferencedSOPClassUID",
        "TransferSyntaxUID",
        "MediaStorageSOPClassUID",
        "MediaStorageSOPInstanceUID",
    }
)

# DICOM.md's "Keep" list, verbatim.
KEEP_TAGS: frozenset[str] = frozenset(
    {
        "Modality",
        "BodyPartExamined",
        "PatientSex",
        "Rows",
        "Columns",
        "PixelSpacing",
        "SliceThickness",
        "SpacingBetweenSlices",
        "ImageOrientationPatient",
        "ImagePositionPatient",
        "RescaleSlope",
        "RescaleIntercept",
        "PhotometricInterpretation",
        "BitsAllocated",
        "BitsStored",
        "HighBit",
        "PixelRepresentation",
        "SamplesPerPixel",
        "WindowCenter",
        "WindowWidth",
    }
)

# DICOM.md: "Apply the same offset to StudyDate, SeriesDate, AcquisitionDate, ContentDate."
DATE_TAGS_TO_SHIFT: tuple[str, ...] = (
    "StudyDate",
    "SeriesDate",
    "AcquisitionDate",
    "ContentDate",
)

# Empty by design. DICOM.md: "Whitelist-and-keep only if a specific private tag is
# demonstrably needed. Never blacklist." No ref names one, so this stays empty until a
# concrete, reviewed need arises. Versioned so any future addition is auditable in
# transform_runs.parameters without that field ever containing a secret.
PRIVATE_TAG_WHITELIST: frozenset[tuple[int, int]] = frozenset()
PRIVATE_TAG_WHITELIST_VERSION = "1"

# Bumped on any change to TAG_PROFILE / UID_REMAP_TAG_NAMES / KEEP_TAGS. Folds into every
# S4/S5 transform_run's idempotency key.
PROFILE_VERSION = "1"

# restricted.uid_map.uid_type classification for each remapped tag name -- matches
# DATA-MODEL.md's documented vocabulary (sop | series | study | frame_of_reference) for
# the four that group clinical data; InstanceCreatorUID doesn't fit that vocabulary (it
# identifies equipment/software, not a study/series/instance grouping) so it gets its own
# category, extending the comment-only enum the same way S1-S3 already extended
# quarantine.reason_code/transform_runs.subject_type beyond their originally-documented sets.
UID_TAG_NAME_TO_UID_TYPE: dict[str, str] = {
    "SOPInstanceUID": "sop",
    "ReferencedSOPInstanceUID": "sop",
    "StudyInstanceUID": "study",
    "SeriesInstanceUID": "series",
    "FrameOfReferenceUID": "frame_of_reference",
    "InstanceCreatorUID": "instance_creator",
}
