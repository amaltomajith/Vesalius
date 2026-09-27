"""Public mode and the single admin login (deployment).

Locally (``PHANTOM_PUBLIC`` unset) nothing changes: the demo is bound to 127.0.0.1 and every route
is open, as before. On a public deployment (``PHANTOM_PUBLIC=1``) everyone can LOOK (landing,
app, studies, atlas, gold files, lineage, reports, proof verdicts), and only the admin can DO
anything: upload a scan, start a run, run a proof, ingest, or ask the paid AI. Public uploads
are off because this project must never receive real patient data (CLAUDE.md hard rule 1).

Standard library only: scrypt password hashing and an HMAC-signed, expiring session cookie.

    python -m api.auth hash        # prints PHANTOM_ADMIN_PASSWORD_HASH for a password you type
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

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

COOKIE = "phantom_admin"
SESSION_SECONDS = 12 * 3600
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_MAX_FAILS, _FAIL_WINDOW = 5, 600.0  # per client IP: 5 wrong passwords per 10 minutes


def public_mode() -> bool:
    return os.environ.get("PHANTOM_PUBLIC", "").strip().lower() in {"1", "true", "yes"}


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
    s = os.environ.get("PHANTOM_SESSION_SECRET", "")
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
    """Dependency on every route that changes anything. A no-op unless PHANTOM_PUBLIC=1."""
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Sign in as the admin to do this.")


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
    stored = os.environ.get("PHANTOM_ADMIN_PASSWORD_HASH", "")
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
