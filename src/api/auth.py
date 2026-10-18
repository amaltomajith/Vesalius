"""Public mode and the single admin login (deployment).

Locally (``VESALIUS_PUBLIC`` unset) nothing changes: the demo is bound to 127.0.0.1 and every route
is open, as before. On a public deployment (``VESALIUS_PUBLIC=1``) everyone can LOOK (landing,
app, studies, atlas, gold files, lineage, reports, proof verdicts), and only the admin can DO
anything: upload a scan, start a run, run a proof, ingest. Public uploads are off because this
project must never receive real patient data (CLAUDE.md hard rule 1). The one exception is the AI
explanations (user decision 2026-10-18): open to visitors, limited per client IP and per day
(`ai_limit`) so the free Groq quota cannot be drained.

Standard library only: scrypt password hashing and an HMAC-signed, expiring session cookie.

    python -m api.auth hash        # prints VESALIUS_ADMIN_PASSWORD_HASH for a password you type
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

COOKIE = "vesalius_admin"
SESSION_SECONDS = 12 * 3600
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_MAX_FAILS, _FAIL_WINDOW = 5, 600.0  # per client IP: 5 wrong passwords per 10 minutes
AI_PER_IP, AI_WINDOW = 10, 3600.0      # per visitor: 10 AI explanations an hour
AI_DAILY_CAP_DEFAULT = 300             # the whole site, per UTC day (VESALIUS_AI_DAILY_CAP)


def public_mode() -> bool:
    return os.environ.get("VESALIUS_PUBLIC", "").strip().lower() in {"1", "true", "yes"}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=2**26, **_SCRYPT)
    b64 = base64.urlsafe_b64encode
    # ":" not "$": Docker Compose interpolates $ inside .env values, which would silently
    # corrupt the hash and lock the admin out. ":" is outside the base64 alphabet.
    return f"scrypt:{b64(salt).decode()}:{b64(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, dk_b64 = stored.split(":")
        if scheme != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(dk_b64)
    except ValueError:
        return False
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=len(expected), maxmem=2**26, **_SCRYPT)
    return hmac.compare_digest(dk, expected)


def _secret() -> bytes:
    s = os.environ.get("VESALIUS_SESSION_SECRET", "")
    if len(s) < 32:
        raise HTTPException(status_code=503, detail="Admin login is not configured on this server.")
    return s.encode()


def make_session(now: float | None = None) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int((time.time() if now is None else now) + SESSION_SECONDS)}).encode()
    ).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def valid_session(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    try:
        expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    except HTTPException:
        return False
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        exp = int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (ValueError, KeyError, TypeError):
        return False
    return (time.time() if now is None else now) < exp


def is_admin(request: Request) -> bool:
    return not public_mode() or valid_session(request.cookies.get(COOKIE))


def require_admin(request: Request) -> None:
    """Dependency on every route that changes anything. A no-op unless VESALIUS_PUBLIC=1."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Sign in as the admin to do this.")


# ---- the AI: open to visitors, but limited ----------------------------------------------------
_ai_calls: dict[str, deque[float]] = defaultdict(deque)
_ai_day: dict[str, Any] = {"day": "", "count": 0}
_clock = time.monotonic                                            # tests move these
_today = lambda: time.strftime("%Y-%m-%d", time.gmtime())  # noqa: E731


def ai_daily_cap() -> int:
    try:
        return max(0, int(os.environ.get("VESALIUS_AI_DAILY_CAP", AI_DAILY_CAP_DEFAULT)))
    except ValueError:
        return AI_DAILY_CAP_DEFAULT


def ai_limit(request: Request) -> None:
    """Dependency on the AI routes. A no-op locally and for the admin; on a public deployment each
    visitor gets AI_PER_IP explanations per AI_WINDOW, and the site VESALIUS_AI_DAILY_CAP per
    day."""
    if not public_mode() or is_admin(request):
        return
    ip = request.client.host if request.client else "?"
    now = _clock()
    calls = _ai_calls[ip]
    while calls and now - calls[0] > AI_WINDOW:
        calls.popleft()
    if len(calls) >= AI_PER_IP:
        raise HTTPException(
            status_code=429, detail="That is ten explanations this hour. Try again a little later."
        )
    today = _today()
    if _ai_day["day"] != today:
        _ai_day.update(day=today, count=0)
    if _ai_day["count"] >= ai_daily_cap():
        raise HTTPException(
            status_code=429,
            detail="The AI has reached today's limit on this showcase. Try tomorrow.",
        )
    calls.append(now)
    _ai_day["count"] += 1


# ---- routes -------------------------------------------------------------------------------------

router = APIRouter(prefix="/auth", tags=["auth"])
_fails: dict[str, deque[float]] = defaultdict(deque)


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.get("/me")
def me(request: Request) -> dict[str, bool]:
    return {"public": public_mode(), "admin": is_admin(request)}


@router.post("/login")
def login(body: Login, request: Request, response: Response) -> dict[str, bool]:
    if not public_mode():
        return {"public": False, "admin": True}
    ip = request.client.host if request.client else "?"
    now = time.monotonic()
    fails = _fails[ip]
    while fails and now - fails[0] > _FAIL_WINDOW:
        fails.popleft()
    if len(fails) >= _MAX_FAILS:
        raise HTTPException(
            status_code=429, detail="Too many attempts. Try again in a few minutes."
        )
    stored = os.environ.get("VESALIUS_ADMIN_PASSWORD_HASH", "")
    if not stored or not verify_password(body.password, stored):
        fails.append(now)
        raise HTTPException(status_code=401, detail="That password is not right.")
    fails.clear()
    response.set_cookie(
        COOKIE,
        make_session(),
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return {"public": True, "admin": True}


@router.post("/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return {"public": public_mode(), "admin": not public_mode()}


if __name__ == "__main__":
    if sys.argv[1:] != ["hash"]:
        sys.exit("usage: python -m api.auth hash")
    first = getpass.getpass("Admin password: ")
    if len(first) < 12:
        sys.exit("Use at least 12 characters.")
    if getpass.getpass("Again: ") != first:
        sys.exit("The two passwords differ.")
    print(hash_password(first))
