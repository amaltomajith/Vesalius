"""Plain-English wording for the interface: what each pipeline step does, and what every reason
code the system can emit means.

`tests/unit/test_demo_explain.py` scans the source tree for every reason code (`QG_*`, `S<n>_*`,
`GATE_*`) and every skip reason, and fails if any lacks an entry here -- so the page can never show a
bare code with no explanation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    key: str
    stages: tuple[str, ...]  # pipeline stages this step covers
    title: str
    what: str  # one sentence, no jargon


STEPS: tuple[Step, ...] = (
    Step(
        "receive",
        ("S1",),
        "Receive the files",
        "Copy the uploaded scan into secure storage exactly as received.",
    ),
    Step(
        "catalog",
        ("S2",),
        "Read and catalogue",
        "Read each image's details and file them under the right patient study and series.",
    ),
    Step(
        "quality",
        ("S3",),
        "Quality check",
        "Check the scan is complete and consistent enough to build a 3D volume from.",
    ),
    Step(
        "privacy",
        ("S4", "S5"),
        "Remove patient identifiers",
        "Strip names, IDs, dates, hidden vendor data and text burned into the pictures, then check nothing was missed.",
    ),
    Step(
        "volume",
        ("S6",),
        "Build the 3D volume",
        "Stack the images into one 3D block with correct orientation and evenly spaced voxels.",
    ),
    Step(
        "face",
        ("S7",),
        "Remove the face",
        "A 3D model of a head can identify a person, so the face is cut away before any surface is made.",
    ),
    Step(
        "segment",
        ("S8",),
        "Pick out the tissue",
        "Select the part of the scan to model (skin surface or bone) by its density.",
    ),
    Step(
        "surface",
        ("S9",),
        "Make a 3D surface",
        "Turn the selected tissue into a surface made of small triangles.",
    ),
    Step(
        "smooth",
        ("S10",),
        "Smooth and simplify",
        "Smooth the surface and reduce the triangle count so it opens quickly.",
    ),
    Step(
        "package",
        ("S11",),
        "Package the results",
        "Save the 3D model, a preview picture and a record of exactly how it was made.",
    ),
)

STEP_BY_KEY = {s.key: s for s in STEPS}

GLOSSARY: dict[str, str] = {
    # -- quality gate (S3)
    "QG_TOO_FEW_SLICES": "This scan has fewer than 3 images. A 3D volume needs at least 3.",
    "QG_SPACING_NONUNIFORM": "The gaps between the images are uneven, so a 3D volume would be distorted.",
    "QG_SPACING_ZERO": "Two or more images sit at the same position, so the spacing cannot be worked out.",
    "QG_SLICE_GAP": "One or more images are missing from the middle of the scan.",
    "QG_ORIENTATION_MIXED": "The images do not all face the same direction.",
    "QG_MISSING_GEOMETRY": "The images do not say where they are in space, so they cannot be stacked.",
    "QG_MISSING_RESCALE": "The images lack the numbers that convert pixel values to Hounsfield density units.",
    "QG_PIXEL_DECODE_FAILED": "The picture data inside one of the images cannot be decoded. It may be damaged, or stored in a compression format this system does not support.",
    "QG_DIMENSION_MISMATCH": "The images are not all the same size.",
    "QG_BODYPART_UNRESOLVED": "The scan does not clearly say which part of the body it shows. This matters because head scans need the face removed, so an unknown body part is treated as unsafe. If you know what it shows, say so in the upload form and try again.",
    "QG_MODALITY_UNSUPPORTED": "This is not a CT or MR scan, which are the only types this pipeline builds volumes from.",
    "QG_DUPLICATE_CONTENT": "These exact images were already received earlier.",
    # -- ingest / parse (S1, S2)
    "S1_UNREADABLE_ARCHIVE": "The uploaded file could not be opened as a zip archive.",
    "S1_ZERO_DICOM_FOUND": "The zip did not contain any DICOM medical image files.",
    "S2_CORRUPT_HEADER": "The details at the top of an image file are damaged and cannot be read.",
    "S2_UNREADABLE_DATASET": "An image file could not be read at all.",
    "S3_UNREADABLE_INSTANCE": "An image that was read earlier could not be re-read for the quality check (a storage problem).",
    # -- privacy (S4, S5)
    "S4_TAG_WRITE_FAILED": "A piece of identifying information could not be cleared, so the image was held back.",
    "S4_BURNED_IN_TEXT_LOW_CONFIDENCE": "Possible text was found inside the picture but too faintly to remove safely, so a person needs to look.",
    "S4_UNREADABLE_INSTANCE": "An image could not be re-read for privacy cleaning (a storage problem).",
    "S5_PROHIBITED_TAG_PRESENT": "The independent privacy check still found an identifying field after cleaning, so the image was held back.",
    "S5_PRIVATE_TAG_PRESENT": "The independent privacy check found hidden vendor data that should have been removed.",
    "S5_UID_NOT_REMAPPED": "An original identifier was still present after cleaning.",
    "S5_BURNED_IN_TEXT_DETECTED": "The independent check still read text inside the picture after redaction.",
    "S5_BURNED_IN_ANNOTATION_MISMATCH": "The image claims to contain burned-in text but the cleaning record disagrees.",
    # -- volume (S6)
    "S6_UNREADABLE_INSTANCE": "A cleaned image could not be read back to build the volume (a storage problem).",
    "S6_TOO_FEW_SLICES": "Fewer than 3 usable images reached the volume step.",
    "S6_ORIENTATION_MIXED": "The cleaned images do not all face the same direction.",
    "S6_GEOMETRY_DRIFT": "The image positions changed between the quality check and the volume step.",
    "S6_SPACING_NONUNIFORM": "The gaps between images are uneven; the pipeline refuses to guess or fill them in.",
    # -- face removal (S7)
    "S7_MODALITY_UNSUPPORTED": "Face removal is only defined for CT scans.",
    "S7_AFFINE_INVALID": "The volume's orientation information is unusable, so the front of the head cannot be located.",
    "S7_AFFINE_MISMATCH": "The volume's orientation record disagrees with its own file header.",
    "S7_NO_HEAD_FOUND": "No plausible head was found in a scan marked as needing face removal, so nothing was passed on.",
    "S7_NO_EFFECT": "Face removal ran but changed almost nothing, so the scan was not passed on.",
    "S7_CHANGE_IMPLAUSIBLE": "Face removal would have deleted an implausibly large part of the body, so the scan was not passed on.",
    "S7_EFFICACY_CHECK_FAILED": "An independent check judged the face removal insufficient, so the scan was not passed on.",
    "S7_OUTPUT_HEADER_MISMATCH": "The face-removed volume's header differs from the original's.",
    "S7_INPUT_TAMPERED": "The stored volume does not match its recorded fingerprint. It may have been altered.",
    "S7_UNREADABLE_INPUT": "The volume could not be read for face removal (a storage problem).",
    # -- segmentation / surface / mesh / export (S8-S11)
    "S8_STRUCTURES_EMPTY": "The anatomy model found no organs or bones it recognises in this scan.",
    "S8_STRUCTURES_GEOMETRY_MISMATCH": "The organ map did not line up exactly with the scan, so it was not trusted.",
    "S9_STRUCTURES_NONE_MESHED": "None of the organs or bones found were large enough to make a surface.",
    "S9_STRUCTURES_INPUT_TAMPERED": "A stored organ-map file changed after it was recorded, so it was not used.",
    "S10_STRUCTURES_INPUT_TAMPERED": "A stored organ-surface file changed after it was recorded, so it was not used.",
    "S11_STRUCTURES_INPUT_TAMPERED": "A stored simplified-surface file changed after it was recorded, so it was not used.",
    "series_has_no_s8_structures": "The organ map for this scan has not been made yet.",
    "series_has_no_s9_structures": "The organ surfaces for this scan have not been made yet.",
    "series_has_no_s10_structures": "The simplified organ surfaces for this scan have not been made yet.",
    "S8_MODALITY_UNSUPPORTED": "Tissue selection by density only works on CT scans.",
    "S8_EMPTY_MASK": "No tissue fell inside the chosen density range.",
    "S8_MASK_TOO_LARGE": "The chosen density range selected almost the whole volume, which means it is wrong for this scan.",
    "S8_GEOMETRY_MISMATCH": "The volume's size or orientation disagrees with its own description.",
    "S8_OUTPUT_HEADER_MISMATCH": "The selected-tissue map does not line up with the volume it came from.",
    "S8_UNREADABLE_INPUT": "The volume could not be read for tissue selection (a storage problem).",
    "S9_MODALITY_UNSUPPORTED": "Surface building only works on CT scans.",
    "S9_AFFINE_INVALID": "The volume's orientation information is unusable for placing a surface in space.",
    "S9_GEOMETRY_MISMATCH": "The tissue map does not line up with the volume.",
    "S9_INPUT_TAMPERED": "The stored tissue map does not match its recorded fingerprint.",
    "S9_ZERO_FACES": "No surface could be made from the selected tissue.",
    "S9_INVARIANT_VIOLATION": "The generated surface failed its own internal consistency checks.",
    "S9_OUTPUT_MISMATCH": "The saved surface differs from what was computed.",
    "S9_UNREADABLE_INPUT": "An input for surface building could not be read (a storage problem).",
    "S10_UNREADABLE_INPUT": "The surface could not be read for smoothing (a storage problem).",
    "S10_INPUT_TAMPERED": "The stored surface does not match its recorded fingerprint.",
    "S10_ZERO_FACES": "Nothing was left of the surface after cleaning it up.",
    "S10_OUTPUT_MISMATCH": "The saved smoothed surface differs from what was computed.",
    "S10_POSTCONDITION_FAILED": "The smoothed surface failed its own internal consistency checks.",
    "S11_UNREADABLE_INPUT": "The finished surface could not be read for packaging (a storage problem).",
    "S11_INPUT_TAMPERED": "The stored surface does not match its recorded fingerprint.",
    "S11_OUTPUT_MISMATCH": "A packaged result differs from what was computed.",
    # -- the face-removal gate (S7 -> S8/S9/S11 refusals)
    "GATE_SERIES_NOT_FOUND": "The scan could not be found.",
    "GATE_SERIES_NOT_PASSED": "The scan did not pass the quality check, so nothing was built from it.",
    "GATE_NO_S6_VOLUME": "There is no 3D volume yet.",
    "GATE_NO_S7_RUN": "The face-removal step has not been run for this scan. The pipeline refuses to build a 3D model of a head without it.",
    "GATE_S7_RUN_STALE": "The face-removal record belongs to an older version of this scan.",
    "GATE_S7_RUN_NOT_SUCCESS": "Face removal did not finish successfully, so nothing was built.",
    "GATE_DECISION_UNEXPLAINED": "The record does not explain why face removal was or was not needed. Every decision must be explained.",
    "GATE_VOCAB_STALE": "The body-part rules changed since face removal was decided.",
    "GATE_DECISION_UNVERIFIABLE": "The face-removal decision could not be re-checked.",
    "GATE_DECISION_STALE": "Face removal is required now but was not applied.",
    "GATE_REPORT_MISSING": "The face-removal report is missing.",
    "GATE_REPORT_HASH_MISMATCH": "The face-removal report does not match its recorded fingerprint.",
    "GATE_REPORT_DISAGREES": "The face-removal report disagrees with the recorded decision.",
    "GATE_DEFACED_ARTIFACT_MISSING": "The face-removed volume is missing.",
    "GATE_DEFACED_NOT_IN_RUN_OUTPUTS": "The face-removed volume is not among the outputs recorded for that step.",
    "GATE_DEFACED_HASH_MISMATCH": "The face-removed volume does not match its recorded fingerprint. It may have been swapped for the original.",
    "GATE_S6_HASH_MISMATCH": "The volume does not match its recorded fingerprint.",
    "GATE_FORGED": "An internal safety check was bypassed.",
    # -- skip reasons (a step that chose not to run, which is not an error)
    "defacing_gate_refused": "The face-removal safety gate refused to hand over the volume.",
    "series_has_no_s6_volume": "There is no 3D volume to work from yet.",
    "series_has_no_s8_mask": "The tissue-selection step has not produced anything to build on.",
    "series_has_no_s9_mesh": "The surface-building step has not produced anything to smooth.",
    "series_has_no_s10_mesh": "The smoothing step has not produced anything to package.",
    "series_not_passed_quality_gate": "The scan did not pass the quality check.",
    "duplicate_content": "These exact images were already received earlier.",
}

_CODE_RE = re.compile(r"[A-Za-z0-9_]+(?::[A-Za-z0-9_]+)?")


def explain(code: str | None) -> str:
    """Plain-English meaning of a reason code (or a `skip:GATE_CODE` pair). Never raises and never
    returns the bare code alone."""
    if not code:
        return "No reason was recorded."
    head, _, tail = code.partition(":")
    text = GLOSSARY.get(head)
    if tail and tail in GLOSSARY:
        text = f"{text or GLOSSARY.get(head, '')} {GLOSSARY[tail]}".strip()
    if text:
        return text
    if _CODE_RE.fullmatch(code):
        return "The pipeline stopped this scan for a reason it has no plain-language description for yet."
    return "The pipeline stopped this scan."
