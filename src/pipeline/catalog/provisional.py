"""Sentinel/provisional values for NOT-NULL columns that S2 must populate but does not
own the real computation for. Centralized here so S3/S4 know exactly what they are
expected to overwrite.

- Pseudo UIDs (`uid_map.pseudo_uid`, and the `pseudo_*` columns in studies/series/
  instances that mirror it): a provisional random value at S2, replaced by S4's
  deterministic HMAC-keyed scheme (DICOM.md) once DEID_UID_SECRET is available.
- `series.quality_status`: 'pending' at S2, replaced by S3's pass/quarantined verdict.
- `studies.deface_required`: S2 does a static body-part vocabulary lookup and defaults to
  True (fail closed) when the body part is unresolved -- S3 is where quarantine-on-
  unresolved is actually enforced.
"""

from __future__ import annotations

from uuid import uuid4

PROVISIONAL_QUALITY_STATUS = "pending"


def provisional_pseudo_uid() -> str:
    """Placeholder pseudo UID. Never equal to any original UID (different format
    entirely -- a random UUID4 string vs. a DICOM UID), and unique per call.
    """
    return f"provisional.{uuid4()}"
