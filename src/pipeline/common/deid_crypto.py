"""Deterministic, HMAC-keyed pseudonymization. refs/DICOM.md Part 1's UID remapping and
date-shifting code, near-verbatim. `secret` is always an explicit parameter, never read
from a module-level global, so callers are forced to source it from Settings (never a bare
default) and so tests can call these with a fixed test secret with zero DB/env dependency.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import date, timedelta

UID_ROOT = "2.25"  # 2.25 + decimal integer is a valid unregistered UID root


def pseudo_uid(original: str, secret: str) -> str:
    """DICOM.md's pseudo_uid(), verbatim. UID components must be numeric, no leading
    zeros, total length <= 64 -- satisfied since digest[:12] yields at most a 96-bit
    integer (<=29 decimal digits), well under the limit with the "2.25." prefix.
    """
    digest = hmac.new(secret.encode(), original.encode(), hashlib.sha256).digest()
    value = int.from_bytes(digest[:12], "big")
    return f"{UID_ROOT}.{value}"


def pseudo_patient_id(original_patient_id: str, secret: str) -> str:
    """Not specified by DICOM.md (which only gives pseudo_uid() for DICOM UIDs and
    patient_offset_days() for the date shift) -- a same-style HMAC scheme for the
    catalog-level patient pseudonym. Reuses the UID secret rather than a third env var,
    since a patient pseudonym is identifier-shaped like a UID, not date-shaped, and it is
    never written into a DICOM tag itself (PatientID's tag action is Z, zero-length) --
    only ever stored in the catalog.
    """
    digest = hmac.new(secret.encode(), original_patient_id.encode(), hashlib.sha256).digest()
    return f"PP-{digest.hex()[:32]}"


def patient_offset_days(original_patient_id: str, secret: str) -> int:
    """DICOM.md's patient_offset_days(), verbatim: 1-3650 days back, deterministic per
    original patient id.
    """
    digest = hmac.new(secret.encode(), original_patient_id.encode(), hashlib.sha256).digest()
    return -(int.from_bytes(digest[:4], "big") % 3650 + 1)


def shift_date(original: date, offset_days: int) -> date:
    return original + timedelta(days=offset_days)
