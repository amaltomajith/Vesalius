"""S12's fixed serving policy. Nothing here changes any stored data (S12 only reads), so none of it
is versioned into transform_runs -- S12 records no transform_run (it produces no artifact).
"""

from __future__ import annotations

STAGE = "S12"

# The one zone the API may ever serve. Every artifact query filters on it.
SERVED_ZONE = "gold"

# The contract whose version every data-carrying response reports (DATA-MODEL.md).
CONTRACT_NAME = "volume_contract"

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# run.reason / quarantine.reason_code are free text in the catalog: failed runs record
# `ExceptionType: message`, which can carry arbitrary tag values. Only values shaped like a
# reason CODE are returned; anything else is replaced.
SAFE_REASON_PATTERN = r"[A-Za-z0-9_:.\-]{1,80}"
REDACTED = "redacted"

# Stages S1-S5 record per-INSTANCE runs whose parameters can carry input-derived text (S1 stores
# the archive member's original filename as `source_name` -- `SMITH_JOHN_CT.dcm` is PHI). For
# those stages only the `*_version` keys (which identify code, not data) are served. S6+ record
# code-defined parameters and pass through.
INPUT_DERIVED_STAGES_MAX = 5
SERVED_KEY_SUFFIX_EARLY = "_version"
