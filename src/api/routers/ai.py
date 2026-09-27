"""Optional AI explanations (refs/INTEGRATION.md §5). OFF unless the server has GROQ_API_KEY.

Two routes, two rules:

* ``POST /ai/explain/study`` explains what a pipeline study's MEASUREMENTS indicate. The request
  carries only ``{study_id, node}``; the server reads the study's gold ``structures.json`` itself
  and builds the context from an allow-list of fields (measurements and study facts). No pixels,
  no identifiers, nothing from ``restricted`` (CLAUDE.md hard rules 8 and 10). Extra request
  fields are refused, so an image can never be sent for a patient study.
* ``POST /ai/explain/atlas`` is for the full-body atlas only (not a patient): the node must exist
  in ``assets/atlas/atlas.json``; a snapshot of the view may be attached (Holoscan's vision path).

The key never leaves the server. Groq is an external service, used only when a key is set.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.auth import require_admin
from api.dependencies import serving_session_dependency, settings_dependency
from api.routers import ui
from pipeline.common.config import Settings
from pipeline.stages.s12_serve import queries
from pipeline.storage.object_store import get_bytes, gold_client

router = APIRouter(prefix="/ai", tags=["ai"])
ServingSession = Annotated[Session, Depends(serving_session_dependency)]

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
MAX_IMAGE_BYTES = 6_000_000  # a data: URL of one canvas frame

STUDY_RULES = (
    "You explain CT measurements for a 3D visualisation tool. Describe what the supplied "
    "numbers indicate, referencing typical adult ranges where you know them. Say plainly that "
    "this is visualisation support, not diagnosis. Never assert a finding that a supplied number "
    "does not support. Do not speculate about pathology. At most 110 words, plain prose."
)
ATLAS_RULES = (
    "You are an anatomy tutor inside a 3D atlas of a generic human body (not a patient). "
    "Explain the selected structure: what it is, where it sits, what it does, and one "
    "clinically relevant fact. At most 110 words, plain prose."
)


def _key() -> str | None:
    return os.environ.get("GROQ_API_KEY") or None


def _require_key() -> str:
    key = _key()
    if not key:
        raise HTTPException(
            status_code=503, detail="AI explanation is off: no GROQ_API_KEY on the server."
        )
    return key


class StudyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # no image field can ride along
    study_id: UUID
    node: str = Field(pattern=r"^[A-Za-z0-9_]{1,80}$")


class AtlasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=200)
    image: str | None = Field(default=None, max_length=MAX_IMAGE_BYTES)


@router.get("/status")
def status() -> dict[str, bool]:
    on = _key() is not None
    return {"enabled": on, "vision": on}


# ---- context: an allow-list, never the raw manifest ---------------------------------------------

STRUCTURE_FIELDS = ("label", "volume_ml", "hu_mean", "hu_std")
STUDY_FIELDS = ("modality", "body_part", "voxel_spacing_mm")


def study_context(manifest: dict[str, Any], node: str) -> dict[str, Any]:
    """Only named measurement and study-fact fields; anything else in the manifest is ignored."""
    for group in manifest.get("groups", []):
        for s in group.get("structures", []):
            if s.get("node") == node:
                ctx: dict[str, Any] = {k: s[k] for k in STRUCTURE_FIELDS if k in s}
                bbox = s.get("bbox_mm")
                if isinstance(bbox, list) and len(bbox) == 2:
                    ctx["extent_mm"] = [
                        round(abs(b - a), 1) for a, b in zip(bbox[0], bbox[1], strict=True)
                    ]
                ctx["system"] = group.get("label")
                study = manifest.get("study", {})
                ctx["study"] = {k: study[k] for k in STUDY_FIELDS if k in study}
                ctx["face_removed_from_head_structures"] = bool(
                    manifest.get("deface", {}).get("structures_cut")
                )
                return ctx
    raise HTTPException(status_code=404, detail="That structure is not in this study.")


def _gold_manifest(session: Session, settings: Settings, study_id: UUID) -> dict[str, Any]:
    key = queries.structures_manifest_key(session, study_id)  # gold only
    if key is None:
        raise HTTPException(status_code=404, detail="study not found")
    return dict(json.loads(get_bytes(gold_client(settings), settings.minio_gold_bucket, key)))


@lru_cache(maxsize=1)
def _atlas_nodes() -> frozenset[str]:
    base = ui.atlas_dir()
    if base is None:
        return frozenset()
    m = json.loads((base / "atlas.json").read_text(encoding="utf-8"))
    return frozenset(s["node"] for g in m["groups"] for s in g["structures"])


# ---- upstream -----------------------------------------------------------------------------------


def open_stream(key: str, payload: dict[str, Any]) -> Iterator[str]:
    """Streams text deltas from Groq's OpenAI-compatible endpoint. Opened BEFORE the response
    starts, so an upstream error becomes an HTTP error instead of a broken stream."""
    req = urllib.request.Request(  # noqa: S310 -- fixed https URL
        GROQ_URL,
        data=json.dumps(
            {**payload, "stream": True, "temperature": 0.2, "max_tokens": 260}
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=60)  # noqa: S310
    except urllib.error.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"The AI service said {exc.code}.") from exc
    except OSError as exc:
        raise HTTPException(status_code=502, detail="The AI service could not be reached.") from exc

    def lines() -> Iterator[str]:
        with resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    delta = json.loads(line[6:])["choices"][0]["delta"].get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    return lines()


@router.post("/explain/study", dependencies=[Depends(require_admin)])
def explain_study(
    body: StudyRequest,
    session: ServingSession,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> StreamingResponse:
    key = _require_key()
    context = study_context(_gold_manifest(session, settings, body.study_id), body.node)
    payload = {
        "model": TEXT_MODEL,
        "messages": [
            {"role": "system", "content": STUDY_RULES},
            {
                "role": "user",
                "content": "Measurements from the pipeline:\n"
                + json.dumps(context, sort_keys=True),
            },
        ],
    }
    return StreamingResponse(open_stream(key, payload), media_type="text/plain; charset=utf-8")


_DATA_URL = re.compile(r"^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$")


@router.post("/explain/atlas", dependencies=[Depends(require_admin)])
def explain_atlas(body: AtlasRequest) -> StreamingResponse:
    key = _require_key()
    if body.node not in _atlas_nodes():
        raise HTTPException(status_code=404, detail="That structure is not in the atlas.")
    ask = f"Selected structure: {body.label}."
    if body.image:
        if not _DATA_URL.match(body.image):
            raise HTTPException(
                status_code=422, detail="image must be a PNG, JPEG or WebP data URL"
            )
        content: Any = [
            {"type": "text", "text": ask + " The image is the current view of the 3D atlas."},
            {"type": "image_url", "image_url": {"url": body.image}},
        ]
        model = VISION_MODEL
    else:
        content = ask
        model = TEXT_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": ATLAS_RULES},
            {"role": "user", "content": content},
        ],
    }
    return StreamingResponse(open_stream(key, payload), media_type="text/plain; charset=utf-8")
