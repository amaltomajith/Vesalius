"""Health, readiness and idleness.

/health        the process answers (liveness; uptime monitors)
/health/ready  the database and the object store answer too: the live backend is usable. The wake
               function (deploy/azure/wake) polls it after starting the VM.
/health/idle   seconds since the last request that was not a health check, and whether a heavy job
               holds the lock. scripts/deploy/autostop.sh deallocates the VM when it is idle.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api import demo_state
from pipeline.catalog.session import get_serving_session
from pipeline.common.config import get_settings
from pipeline.storage.object_store import gold_client

router = APIRouter()

_clock = time.monotonic  # tests move it
_last = {"t": _clock()}


Next = Callable[[Request], Awaitable[Response]]


async def note_activity(request: Request, call_next: Next) -> Response:
    """HTTP middleware: any request but a health check counts as someone using the backend."""
    if not request.url.path.startswith("/health"):
        _last["t"] = _clock()
    return await call_next(request)


def _database_ok() -> bool:
    with get_serving_session(get_settings()) as session:
        return session.execute(text("SELECT 1")).scalar() == 1


def _store_ok() -> bool:
    settings = get_settings()
    return bool(gold_client(settings).bucket_exists(settings.minio_gold_bucket))


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
def ready() -> dict[str, str] | JSONResponse:
    checks: dict[str, Any] = {"database": _database_ok, "object_store": _store_ok}
    for name, check in checks.items():
        try:
            if not check():
                raise RuntimeError(name)
        except Exception:  # noqa: BLE001 -- any failure means not ready, and says which part
            body = {"status": "starting", "waiting_for": name}
            return JSONResponse(status_code=503, content=body)
    return {"status": "ready"}


@router.get("/health/idle")
def idle() -> dict[str, Any]:
    return {"idle_seconds": round(_clock() - _last["t"], 1), "busy": demo_state.HEAVY_LOCK.locked()}
