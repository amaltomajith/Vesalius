"""Closed body-part vocabulary -- the single source of truth for S2, S3 and S7.

The defacing decision has one safe direction: MORE defacing. So "no defacing required" may
only ever be concluded from a POSITIVE match against an explicit known-non-head list.
Everything else -- a head-adjacent term, an ambiguous one, a term we have never heard of,
or no value at all -- means "required" (or "cannot tell", which S3 quarantines). Treating a
present-but-unrecognised value like `SINUSES` or `ORBITS` as "resolved, not a head" would let
a real face CT skip defacing entirely (that was the previous behaviour; see the S7 plan).

Both sets below are additive to GEOMETRY.md's trigger list. Adding a head-adjacent term only
ever increases defacing. Adding a known-non-head term is the dangerous direction and must be
justified: a term belongs there only if a scan so labelled cannot plausibly image a face.
"""

from __future__ import annotations

import re
from enum import StrEnum

VOCAB_VERSION = "1"

# Separators dropped when normalising, so "C-SPINE" == "CSPINE" and "HEAD_NECK" == "HEADNECK".
_SEPARATORS = re.compile(r"[\s_\-/.]+")
_TOKEN = re.compile(r"[A-Z0-9]+")


class BodyPartClass(StrEnum):
    HEAD_ADJACENT = "head_adjacent"
    KNOWN_NON_HEAD = "known_non_head"
    AMBIGUOUS = "ambiguous"  # could contain a head (bare SPINE, WHOLEBODY, ...)
    UNRECOGNISED = "unrecognised"  # a value we do not know -- never assumed to be non-head
    ABSENT = "absent"


# GEOMETRY.md's 14 trigger values (HEAD, SKULL, BRAIN, FACE, SINUS, NECK, TMJ, ORBIT, MAXILLA,
# MANDIBLE, CSPINE, C-SPINE, HEADNECK, PTS_HEAD) plus additive synonyms.
HEAD_ADJACENT: frozenset[str] = frozenset(
    {
        "HEAD",
        "SKULL",
        "CRANIUM",
        "CRANIAL",
        "BRAIN",
        "FACE",
        "FACIAL",
        "FACIALBONES",
        "MAXILLOFACIAL",
        "SINUS",
        "SINUSES",
        "PARANASAL",
        "NASAL",
        "NECK",
        "TMJ",
        "ORBIT",
        "ORBITS",
        "MAXILLA",
        "MANDIBLE",
        "JAW",
        "EAR",
        "TEMPORAL",
        "TEMPORALBONE",
        "PITUITARY",
        "CSPINE",
        "CERVICAL",
        "CERVICALSPINE",
        "HEADNECK",
        "HEADANDNECK",
        "NECKHEAD",
        "PTSHEAD",
    }
)

# Positive list: a scan labelled with one of these cannot plausibly image a face.
KNOWN_NON_HEAD: frozenset[str] = frozenset(
    {
        "CHEST",
        "THORAX",
        "THORACIC",
        "ABDOMEN",
        "PELVIS",
        "ABDOMENPELVIS",
        "CHESTABDOMEN",
        "CHESTABDPELVIS",
        "CHESTABDOMENPELVIS",
        "HEART",
        "BREAST",
        "LUNG",
        "TSPINE",
        "LSPINE",
        "SSPINE",
        "LUMBAR",
        "COCCYX",
        "EXTREMITY",
        "ARM",
        "LEG",
        "KNEE",
        "HIP",
        "SHOULDER",
        "ELBOW",
        "WRIST",
        "HAND",
        "FOOT",
        "ANKLE",
        "CLAVICLE",
    }
)

# Labels that may or may not include a head: never "not required", never resolved.
AMBIGUOUS: frozenset[str] = frozenset({"SPINE", "WHOLEBODY", "BODY", "TRUNK"})


def normalise(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = _SEPARATORS.sub("", raw.strip().upper())
    return text or None


def classify_body_part(raw: str | None) -> BodyPartClass:
    value = normalise(raw)
    if value is None:
        return BodyPartClass.ABSENT
    if value in HEAD_ADJACENT:
        return BodyPartClass.HEAD_ADJACENT
    if value in KNOWN_NON_HEAD:
        return BodyPartClass.KNOWN_NON_HEAD
    if value in AMBIGUOUS:
        return BodyPartClass.AMBIGUOUS
    return BodyPartClass.UNRECOGNISED


def deface_required(cls: BodyPartClass) -> bool:
    """Not-required is only ever concluded from a positive KNOWN_NON_HEAD match."""
    return cls is not BodyPartClass.KNOWN_NON_HEAD


def infer_from_description(text: str | None) -> str | None:
    """Whole-token match of free text against the closed vocabulary. Returns a normalised
    vocabulary term, or None. Deterministic (first match in text order).

    Whole tokens only, never substrings: substring matching lets `ARM` fire inside `ALARM` or
    `HIP` inside `SHIPPING`, which would conclude "not a head" from noise -- the dangerous
    direction. Adjacent tokens are also tried joined, so `C SPINE` / `L-SPINE` match
    `CSPINE` / `LSPINE`. Any head-adjacent hit wins over any non-head hit, so a description
    mentioning both (`CHEST ... AND NECK`) resolves fail-closed. Ambiguous-only text (bare
    `SPINE`) infers nothing: it cannot support a "not a head" conclusion.

    Callers must never persist `text` itself (free text is the highest PHI risk of any tag);
    only this function's return value may reach the catalog.
    """
    if text is None or not text.strip():
        return None
    tokens = _TOKEN.findall(text.upper())
    candidates: list[str] = []
    for i, token in enumerate(tokens):
        candidates.append(token)
        if i + 1 < len(tokens):
            candidates.append(token + tokens[i + 1])
    for term in candidates:
        if term in HEAD_ADJACENT:
            return term
    for term in candidates:
        if term in KNOWN_NON_HEAD:
            return term
    return None
