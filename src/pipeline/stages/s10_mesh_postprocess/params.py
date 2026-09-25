"""Versioned parameters of S10's mesh post-processing. Everything here changes S10's output, so
all of it is folded into `transform_runs.parameters` (never inlined elsewhere).

Taubin/decimation values are GEOMETRY.md's own example numbers, with one sign corrected -- see
mesh_postprocess.py's module docstring. Unvalidated on real anatomy, same caveat class as every
other S7-S9 threshold.
"""

from __future__ import annotations

STAGE = "S10"
STAGE_VERSION = "1.0.0"
PARAMS_VERSION = "1"

DEFAULT_PRESET = "bone"

TAUBIN_LAMB = 0.5
# GEOMETRY.md's literal example is nu=-0.53; verified empirically (mesh_postprocess.py) that
# this shrinks the mesh continuously under trimesh's actual filter_taubin implementation. The
# sign is corrected here; the magnitude is kept.
TAUBIN_NU = 0.53
TAUBIN_ITERATIONS = 12

# GEOMETRY.md's "detailed model" tier -- the natural default since nothing here is
# web/thumbnail-specific yet; overridable via --target-faces.
DEFAULT_TARGET_FACES = 200_000

# PLY's binary vertex/normal encoding is float32 (verified empirically -- see S9's mesh_io.py,
# same property), so a write -> re-read round trip is not bit-exact even when correct.
VERTEX_ROUND_TRIP_TOLERANCE_MM = 1e-3
NORMAL_ROUND_TRIP_TOLERANCE = 1e-3


def algorithm_params(target_faces: int) -> dict[str, object]:
    return {
        "taubin_lamb": TAUBIN_LAMB,
        "taubin_nu": TAUBIN_NU,
        "taubin_iterations": TAUBIN_ITERATIONS,
        "target_faces": target_faces,
    }
