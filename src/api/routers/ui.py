"""Serves the demo's two pages: the landing page at `/` and the console at `/console`, plus their
script, style, fonts, icons and 3D-viewer library, all from `src/api/static/` (no build step, no
external requests, works offline).

Also serves the Vesalius app (`viewer/`, built by Vite) at `/app/`: the dashboard, the pipeline
runner, the study library, the proofs and the hands-controlled hologram, same origin as the API it
consumes (no CORS). `/app/atlas/` serves the full-body atlas (`assets/atlas/`). The old console
(`/console`) and viewer (`/viewer/`) addresses redirect into it."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
# Revalidate on every load: without this, browsers heuristically reuse an old copy of the page
# after an update. Unchanged files still come back as a cheap 304 via ETag / Last-Modified.
NO_CACHE = {"Cache-Control": "no-cache"}


def app_dir() -> Path | None:
    """The built Vesalius app (viewer/ built by Vite), if there is one: $VESALIUS_APP_DIR, the
    image's copy, or a local build. None when it was never built (the API still runs; /app/ is
    then 404)."""
    candidates = [
        os.environ.get("VESALIUS_APP_DIR"),
        "/app/app_dist",
        str(Path(__file__).resolve().parents[3] / "viewer" / "dist"),
    ]
    for c in candidates:
        if c and (Path(c) / "index.html").is_file():
            return Path(c)
    return None


def atlas_dir() -> Path | None:
    """The full-body atlas (assets/atlas/, built by scripts/build_atlas.py)."""
    candidates = [
        os.environ.get("VESALIUS_ATLAS_DIR"),
        "/app/assets/atlas",
        str(Path(__file__).resolve().parents[3] / "assets" / "atlas"),
    ]
    for c in candidates:
        if c and (Path(c) / "atlas.json").is_file():
            return Path(c)
    return None


# Vite's hashed build assets and ?v=<sha>-keyed atlas files never change under the same URL.
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _inside(base: Path, rel: str) -> Path | None:
    """`rel` resolved under `base`, or None if it escapes it or is not a file."""
    target = (base / rel).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        return None
    return target if target.is_file() else None


router = APIRouter()


class RevalidatingStaticFiles(StaticFiles):
    """`/static`, with every file marked `no-cache` (revalidate, not re-download)."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.update(NO_CACHE)
        return response


@router.get("/", include_in_schema=False)
def landing() -> FileResponse:
    return FileResponse(STATIC_DIR / "landing.html", media_type="text/html", headers=NO_CACHE)


# ---- the Vesalius app (/app) and the addresses it replaced ---------------------------------------


@router.get("/app", include_in_schema=False)
def app_root() -> RedirectResponse:
    return RedirectResponse("/app/", status_code=308)


@router.get("/app/{path:path}", include_in_schema=False)
def app_files(path: str, request: Request) -> Response:
    if path.startswith("atlas/"):
        base = atlas_dir()
        found = _inside(base, path.removeprefix("atlas/")) if base else None
        if found is None:
            raise HTTPException(status_code=404, detail="not found")
        headers = IMMUTABLE if request.query_params.get("v") else NO_CACHE
        return FileResponse(found, headers=headers)
    base = app_dir()
    if base is None:
        raise HTTPException(status_code=404, detail="the Vesalius app has not been built")
    found = _inside(base, path) if path else None
    if found is not None:
        return FileResponse(found, headers=IMMUTABLE if path.startswith("assets/") else NO_CACHE)
    if "." in path.rsplit("/", 1)[-1]:
        raise HTTPException(status_code=404, detail="not found")  # a missing file, not a route
    # a client-side route (/app/studies, /app/hologram/<id>, ...): the app decides what to show
    return FileResponse(base / "index.html", media_type="text/html", headers=NO_CACHE)


@router.get("/console", include_in_schema=False)
def console() -> RedirectResponse:
    return RedirectResponse("/app/pipeline", status_code=308)


@router.get("/viewer", include_in_schema=False)
@router.get("/viewer/{path:path}", include_in_schema=False)
def viewer(request: Request, path: str = "") -> RedirectResponse:
    study = request.query_params.get("study")
    return RedirectResponse(
        f"/app/hologram/{quote(study, safe='')}" if study else "/app/studies", status_code=308
    )
