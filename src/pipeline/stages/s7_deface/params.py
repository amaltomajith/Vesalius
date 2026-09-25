"""Versioned parameters of `geometric_v1`. Everything here changes S7's output, so all of it is
folded into `transform_runs.parameters` (never inlined as literals elsewhere).

Every numeric threshold is an UNVALIDATED PLACEHOLDER tuned against the synthetic head
phantom (tests/fixtures/head_phantom.py) -- the same caveat class as S4's OCR thresholds.
Real-anatomy validation is deferred (see the S7 plan).
"""

from __future__ import annotations

from pipeline.common.hu_presets import PRESETS_V1, PRESETS_VERSION

STAGE = "S7"
STAGE_VERSION = "1.0.0"
PARAMS_VERSION = "1"
METHOD = "geometric_v1"

# --- region (world space; GEOMETRY.md's deface_region(anterior_fraction, superior_fraction)) ---
ANTERIOR_FRACTION = 0.35  # front 35% of the head's A-P extent (nose through orbits/zygoma)
# GEOMETRY.md's example is 0.55, which stops around eye level and leaves nose/lips/chin intact
# (S7 plan section 3.4). 1.0 = the whole head extent, capped by HEAD_HEIGHT_CAP_MM so a FOV
# that also holds neck and shoulders does not get stripped below the chin.
SUPERIOR_FRACTION = 1.0
HEAD_HEIGHT_CAP_MM = 260.0

# --- strip -----------------------------------------------------------------------------------
STRIP_DEPTH_MM = 30.0  # max depth from the outer envelope that may be removed
SEED_DEPTH_MM = 2.0  # a removable component must reach this close to the skin
BONE_FLOOR_HU = 250.0  # deliberately below the 300 preset so partial-volume cortex stops it
TISSUE_MIN_HU = float(PRESETS_V1["skin_surface"]["min"])  # -200: what counts as body

# Opening (to drop speckle / thin tubes) also trims the tips of real protrusions -- the nose tip is
# exactly the voxel that must go. Restore up to this many voxels of tissue back around the opened
# body (a bounded reconstruction: a tube does not regrow along its length).
BODY_RESTORE_ITERATIONS = 3

# --- head sanity (fail closed with S7_NO_HEAD_FOUND) -------------------------------------------
MIN_BODY_VOLUME_ML = 200.0
AP_EXTENT_RANGE_MM = (80.0, 300.0)
MIN_S_EXTENT_MM = 50.0
MIN_BONE_VOXELS = 5000
# Volume / A-P extent / bone count all PASS on a chest CT, so they cannot say "no head". Lateral
# width of the top slab can: a head is ~140-170 mm wide, a chest > 280 mm.
HEAD_TOP_SLAB_MM = 150.0
HEAD_WIDTH_RANGE_MM = (90.0, 230.0)

# --- postconditions ----------------------------------------------------------------------------
MIN_CHANGED_VOXELS = 20_000
MAX_CHANGED_FRACTION_OF_BODY = 0.30

# --- independent verifier ----------------------------------------------------------------------
VERIFIER_VERSION = "1"
VERIFIER_ANTERIOR_FRACTION = 0.45  # wider than the algorithm's, so it is a strict superset
MIN_ANTERIOR_RETREAT_MM = 15.0
MIN_SKIN_DESTROYED_FRACTION = 0.90
VERIFIER_FACE_FRACTION = 0.30  # skin measured in the front 30% (narrower than the algorithm's 35%)
VERIFIER_SKIN_DEPTH_MM = 1.5  # what counts as a skin-surface voxel
VERIFIER_ZONE_MARGIN_MM = 5.0  # slack between two independently-built head masks
VERIFIER_DEPTH_TOLERANCE_MM = 1.0  # removed voxels may sit at most strip + this deep

ROUND_TRIP_TOLERANCE = 1e-3


def algorithm_params() -> dict[str, object]:
    """The `algorithm` block recorded in transform_runs.parameters for a REQUIRED run."""
    return {
        "method": METHOD,
        "anterior_fraction": ANTERIOR_FRACTION,
        "superior_fraction": SUPERIOR_FRACTION,
        "head_height_cap_mm": HEAD_HEIGHT_CAP_MM,
        "strip_depth_mm": STRIP_DEPTH_MM,
        "seed_depth_mm": SEED_DEPTH_MM,
        "bone_floor_hu": BONE_FLOOR_HU,
        "tissue_min_hu": TISSUE_MIN_HU,
        "body_restore_iterations": BODY_RESTORE_ITERATIONS,
        "hu_presets_version": PRESETS_VERSION,
        "removal_value_source": "meta.intensity.air_value",
        "sanity": {
            "min_body_volume_ml": MIN_BODY_VOLUME_ML,
            "ap_extent_range_mm": list(AP_EXTENT_RANGE_MM),
            "min_s_extent_mm": MIN_S_EXTENT_MM,
            "min_bone_voxels": MIN_BONE_VOXELS,
            "head_top_slab_mm": HEAD_TOP_SLAB_MM,
            "head_width_range_mm": list(HEAD_WIDTH_RANGE_MM),
            "min_changed_voxels": MIN_CHANGED_VOXELS,
            "max_changed_fraction_of_body": MAX_CHANGED_FRACTION_OF_BODY,
        },
        "efficacy_thresholds": {
            "min_anterior_retreat_mm": MIN_ANTERIOR_RETREAT_MM,
            "min_skin_destroyed_fraction": MIN_SKIN_DESTROYED_FRACTION,
            "face_fraction": VERIFIER_FACE_FRACTION,
            "skin_depth_mm": VERIFIER_SKIN_DEPTH_MM,
            "zone_fraction": VERIFIER_ANTERIOR_FRACTION,
            "zone_margin_mm": VERIFIER_ZONE_MARGIN_MM,
            "depth_tolerance_mm": VERIFIER_DEPTH_TOLERANCE_MM,
        },
        "verifier_version": VERIFIER_VERSION,
    }
