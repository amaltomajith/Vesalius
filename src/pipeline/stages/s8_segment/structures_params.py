"""Versioned parameters of S8's `structures` mode (refs/INTEGRATION.md §2): anatomical
segmentation into named structures, and S8b's mapping of those labels onto the viewer's body
system groups. Everything here changes S8's output, so all of it is folded into
`transform_runs.parameters` (never inlined elsewhere).

Engine: TotalSegmentator, `total` task only (Apache-2.0 code and weights; see LICENSES.md). Its
licensed subtasks (`face`, `brain_structures`, ...) must never be enabled here.
"""

from __future__ import annotations

from fnmatch import fnmatchcase

MODE = "structures"
ENGINE = "totalsegmentator"
ENGINE_VERSION = "2.18.0"  # pinned in docker/api/Dockerfile; a mismatch at run time is refused
TASK = "total"
FAST = True  # 3 mm model: ~1 min on CPU for a chest CT (measured), vs many minutes at 1.5 mm
LABEL_SET_VERSION = "totalseg_total_v2"
# Head studies: every structure (bone included) is cut to S7's face region before measuring and
# meshing, so no structure keeps the facial skeleton (user decision 2026-09-27; bone was kept by
# the threshold path, a documented residual risk that structures mode closes).
FACE_CUT = "s7_region_v1"

# ---- S8b: label -> group (refs/INTEGRATION.md §2, GROUP_MAP_V1) --------------------------------
# The refs' table verbatim, plus labels that TotalSegmentator's actual `total` vocabulary uses and
# the refs' illustrative table did not list (marked "added"): without them several great vessels
# and the costal cartilages would fall to `other`. `pulmonary_artery` (refs) does not exist in the
# v2 vocabulary; it is kept so the table stays a superset of the refs.
GROUP_MAP_VERSION = "v1"
GROUP_MAP_V1: dict[str, tuple[str, ...]] = {
    "skeletal": (
        "vertebrae_*",
        "rib_*",
        "humerus_*",
        "femur_*",
        "hip_*",
        "scapula_*",
        "clavicula_*",
        "sacrum",
        "skull",
        "sternum",
        # added (TotalSegmentator v2 vocabulary, not in the refs' illustrative table):
        "costal_cartilages",
    ),
    "cardiovascular": (
        "heart*",
        "aorta",
        "pulmonary_artery",
        "iliac_artery_*",
        "iliac_vena_*",
        "inferior_vena_cava",
        "portal_vein_and_splenic_vein",
        # added (TotalSegmentator v2 vocabulary, not in the refs' illustrative table):
        "superior_vena_cava",
        "pulmonary_vein",
        "atrial_appendage_*",
        "brachiocephalic_*",
        "subclavian_artery_*",
        "common_carotid_artery_*",
    ),
    "visceral": (
        "liver",
        "spleen",
        "kidney_left",
        "kidney_right",
        "stomach",
        "pancreas",
        "gallbladder",
        "duodenum",
        "small_bowel",
        "colon",
        "urinary_bladder",
        "esophagus",
    ),
    "respiratory": ("lung_*", "trachea"),
    "muscular": ("autochthon_*", "iliopsoas_*", "gluteus_*"),
    "nervous": ("brain", "spinal_cord"),
    "endocrine": ("adrenal_gland_*", "thyroid_gland"),
}
OTHER_GROUP = "other"  # unmapped labels go here -- never dropped
GROUP_ORDER = (*GROUP_MAP_V1, OTHER_GROUP)
GROUP_LABELS = {g: g.capitalize() for g in GROUP_ORDER}


def group_for(label: str) -> str:
    """The first group whose pattern matches `label` (case-sensitive, labels are lowercase)."""
    for group, patterns in GROUP_MAP_V1.items():
        if any(fnmatchcase(label, pat) for pat in patterns):
            return group
    return OTHER_GROUP


def display_label(label: str) -> str:
    """`kidney_left` -> `Kidney left`; the node name itself is never changed."""
    return label.replace("_", " ").capitalize()


def algorithm_params() -> dict[str, object]:
    return {
        "mode": MODE,
        "engine": ENGINE,
        "engine_version": ENGINE_VERSION,
        "task": TASK,
        "fast": FAST,
        "label_set_version": LABEL_SET_VERSION,
        "face_cut": FACE_CUT,
        "group_map_version": GROUP_MAP_VERSION,
        "group_map": {g: list(p) for g, p in GROUP_MAP_V1.items()},
    }
