"""Public mode (deployment): everyone may look, only the admin may do anything (src/api/auth.py).

The sweep walks the REAL app's route table, so a new mutating route without the admin
dependency fails here without anyone having to remember to add it to a list.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api import auth
from api import main as api_main
from pipeline.common.config import Settings
from pipeline.storage.object_store import gold_presign_client

OPEN_WRITES = {"/auth/login", "/auth/logout"}  # the only non-GET routes a visitor may call


def _all_routes(routes: list[Any]) -> list[APIRoute]:
    """Flattens the route table. Newer FastAPI keeps each included router as a wrapper
    (`_IncludedRouter`, with the router in `original_router`) instead of copying its routes."""
    out: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        inner = getattr(r, "original_router", None)
        if inner is not None:
            out.extend(_all_routes(inner.routes))
    return out


def _mutating_routes() -> list[APIRoute]:
    return [
        r
        for r in _all_routes(api_main.create_app().routes)
        if (r.methods - {"GET", "HEAD"}) and r.path not in OPEN_WRITES
    ]


@pytest.fixture
def public(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("PHANTOM_PUBLIC", "1")
    monkeypatch.setenv("PHANTOM_SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("PHANTOM_ADMIN_PASSWORD_HASH", auth.hash_password("correct horse battery"))
    auth._fails.clear()
    return "correct horse battery"


def test_there_are_mutating_routes_to_protect() -> None:
    paths = {r.path for r in _mutating_routes()}
    assert {
        "/demo/runs/sample", "/demo/runs/upload", "/demo/proofs/{key}", "/ingest",
        "/ai/explain/study", "/ai/explain/atlas",
    } <= paths


@pytest.mark.parametrize("route", _mutating_routes(), ids=lambda r: r.path)
def test_every_mutating_route_requires_the_admin(route: APIRoute) -> None:
    calls = {d.call for d in route.dependant.dependencies}
    assert auth.require_admin in calls, f"{route.path} can be called by anyone in public mode"


def test_a_visitor_is_refused_on_every_mutating_route(public: str) -> None:
    client = TestClient(api_main.create_app())
    for route in _mutating_routes():
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        method = sorted(route.methods - {"GET", "HEAD"})[0]
        r = client.request(method, path, json={})
        assert r.status_code == 401, (route.path, r.status_code)


def test_locally_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHANTOM_PUBLIC", raising=False)
    client = TestClient(api_main.create_app())
    assert client.get("/auth/me").json() == {"public": False, "admin": True}
    # reaches the route itself (400: unknown sample), not the admin check
    assert client.post("/demo/runs/sample", json={"dataset": "nope"}).status_code == 400


def test_the_admin_can_sign_in_and_act(public: str) -> None:
    client = TestClient(api_main.create_app(), base_url="https://testserver")
    assert client.get("/auth/me").json() == {"public": True, "admin": False}
    assert client.post("/auth/login", json={"password": "wrong password"}).status_code == 401
    ok = client.post("/auth/login", json={"password": public})
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert client.get("/auth/me").json() == {"public": True, "admin": True}
    assert client.post("/demo/runs/sample", json={"dataset": "nope"}).status_code == 400
    client.post("/auth/logout")
    client.cookies.clear()
    assert client.post("/demo/runs/sample", json={"dataset": "nope"}).status_code == 401


def test_wrong_passwords_are_rate_limited(public: str) -> None:
    client = TestClient(api_main.create_app())
    codes = [client.post("/auth/login", json={"password": "guess"}).status_code for _ in range(6)]
    assert codes == [401] * 5 + [429]
    # even the right password waits out the window
    assert client.post("/auth/login", json={"password": public}).status_code == 429


def test_forged_or_expired_sessions_are_refused(public: str) -> None:
    good = auth.make_session()
    assert auth.valid_session(good)
    payload, sig = good.rsplit(".", 1)
    assert not auth.valid_session(payload + "." + "0" * len(sig))
    assert not auth.valid_session("garbage")
    assert not auth.valid_session(auth.make_session(now=0))  # expired long ago


def test_passwords_are_stored_as_scrypt_hashes() -> None:
    h = auth.hash_password("correct horse battery")
    assert h.startswith("scrypt:") and "correct" not in h
    # the hash goes into .env, where Docker Compose would interpolate any dollar sign
    assert "$" not in h
    assert auth.verify_password("correct horse battery", h)
    assert not auth.verify_password("Correct horse battery", h)
    assert not auth.verify_password("x", "not-a-hash")


def test_download_links_are_signed_for_the_public_https_host() -> None:
    s = Settings(minio_public_endpoint="files.phantom.example", minio_public_secure=True)
    url = gold_presign_client(s).presigned_get_object("gold", "a/b.glb")
    assert url.startswith("https://files.phantom.example/gold/a/b.glb?")
