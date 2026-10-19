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

OPEN_WRITES = {"/auth/login", "/auth/logout"}  # the only non-GET routes a visitor may call freely
# open to visitors on purpose, each only through its own rate limit (user decisions: the AI
# 2026-10-18; the built-in samples and the proofs 2026-10-19, for the on-demand live backend)
RATE_LIMITED = {
    "/ai/explain/study": auth.ai_limit,
    "/ai/explain/atlas": auth.ai_limit,
    "/demo/runs/sample": auth.run_limit,
    "/demo/proofs/{key}": auth.proof_limit,
}
# hard rule 1: nothing a visitor can send in. Named here so loosening RATE_LIMITED cannot open them.
ADMIN_ONLY = {"/demo/runs/upload", "/ingest"}


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
    monkeypatch.setenv("VESALIUS_PUBLIC", "1")
    monkeypatch.setenv("VESALIUS_SESSION_SECRET", "s" * 48)
    monkeypatch.setenv("VESALIUS_ADMIN_PASSWORD_HASH", auth.hash_password("correct horse battery"))
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
    if route.path in RATE_LIMITED:
        assert RATE_LIMITED[route.path] in calls, f"{route.path} is open without its rate limit"
        return
    assert auth.require_admin in calls, f"{route.path} can be called by anyone in public mode"


def test_nothing_can_be_sent_in_by_a_visitor() -> None:
    routes = {r.path: r for r in _mutating_routes()}
    assert set(routes) >= ADMIN_ONLY and not ADMIN_ONLY & set(RATE_LIMITED)
    for path in ADMIN_ONLY:
        assert auth.require_admin in {d.call for d in routes[path].dependant.dependencies}, path


def test_a_visitor_is_refused_on_every_mutating_route(public: str) -> None:
    client = TestClient(api_main.create_app())
    for route in _mutating_routes():
        if route.path in RATE_LIMITED:
            continue  # tested below: open, then limited
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        method = sorted(route.methods - {"GET", "HEAD"})[0]
        r = client.request(method, path, json={})
        assert r.status_code == 401, (route.path, r.status_code)


def test_locally_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VESALIUS_PUBLIC", raising=False)
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
    # uploads are the admin's alone: signed in, the request gets past the guard to validation
    assert client.post("/demo/runs/upload", data={}).status_code == 422
    client.post("/auth/logout")
    client.cookies.clear()
    assert client.post("/demo/runs/upload", data={}).status_code == 401


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
    s = Settings(minio_public_endpoint="files.vesalius.example", minio_public_secure=True)
    url = gold_presign_client(s).presigned_get_object("gold", "a/b.glb")
    assert url.startswith("https://files.vesalius.example/gold/a/b.glb?")


# ---- the AI: open to visitors, limited -------------------------------------------------------


@pytest.fixture
def ai_open(public: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """A public server with a (fake) Groq key; nothing leaves the process. Returns the clock."""
    from api.routers import ai

    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(ai, "open_stream", lambda key, payload: iter(["ok"]))
    monkeypatch.setattr(ai, "_atlas_nodes", lambda: frozenset({"Femur"}))
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth, "_clock", lambda: clock["t"])
    monkeypatch.setattr(auth, "_today", lambda: "2026-10-19")
    auth._ai_calls.clear()
    auth._ai_day.update(day="", count=0)
    return clock


def _explain(client: TestClient) -> int:
    return client.post("/ai/explain/atlas", json={"node": "Femur", "label": "Femur"}).status_code


def test_a_visitor_may_ask_the_ai_ten_times_an_hour(ai_open: dict[str, float]) -> None:
    client = TestClient(api_main.create_app())
    assert [_explain(client) for _ in range(11)] == [200] * 10 + [429]
    ai_open["t"] += 3601                     # an hour later the window has moved on
    assert _explain(client) == 200


def test_the_site_stops_at_its_daily_ai_cap(
    ai_open: dict[str, float], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VESALIUS_AI_DAILY_CAP", "3")
    clients = [TestClient(api_main.create_app(), client=(f"10.0.0.{i}", 50000)) for i in range(5)]
    codes = [_explain(c) for c in clients]
    assert codes == [200, 200, 200, 429, 429]       # five visitors, three answers, then the cap
    monkeypatch.setattr(auth, "_today", lambda: "2026-10-20")
    assert _explain(clients[4]) == 200               # a new day


def test_the_admin_is_not_limited(ai_open: dict[str, float]) -> None:
    client = TestClient(api_main.create_app(), base_url="https://testserver")
    assert client.post("/auth/login", json={"password": "correct horse battery"}).status_code == 200
    assert all(_explain(client) == 200 for _ in range(15))


def test_locally_the_ai_has_no_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routers import ai

    monkeypatch.delenv("VESALIUS_PUBLIC", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(ai, "open_stream", lambda key, payload: iter(["ok"]))
    monkeypatch.setattr(ai, "_atlas_nodes", lambda: frozenset({"Femur"}))
    auth._ai_calls.clear()
    client = TestClient(api_main.create_app())
    assert all(_explain(client) == 200 for _ in range(15))


# ---- runs on the built-in samples and the proofs: open to visitors, limited -------------------


@pytest.fixture
def heavy_open(public: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """A public server; the limit is checked before the route, so an unknown sample or proof shows
    whether the limit let the call through (400/404) or refused it (429). Nothing ever runs."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(auth, "_clock", lambda: clock["t"])
    monkeypatch.setattr(auth, "_today", lambda: "2026-10-19")
    auth._heavy_calls.clear()
    auth._heavy_day.update(day="", count=0)
    return clock


def _run(client: TestClient) -> int:
    return client.post("/demo/runs/sample", json={"dataset": "not-a-sample"}).status_code


def _prove(client: TestClient) -> int:
    return client.post("/demo/proofs/not-a-proof").status_code


def test_a_visitor_may_run_six_samples_and_three_proofs_an_hour(
    heavy_open: dict[str, float],
) -> None:
    client = TestClient(api_main.create_app())
    assert [_run(client) for _ in range(7)] == [400] * 6 + [429]
    assert [_prove(client) for _ in range(4)] == [404] * 3 + [429]   # its own allowance
    heavy_open["t"] += 3601
    assert (_run(client), _prove(client)) == (400, 404)


def test_the_site_stops_at_its_daily_run_cap(
    heavy_open: dict[str, float], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VESALIUS_RUN_DAILY_CAP", "2")
    clients = [TestClient(api_main.create_app(), client=(f"10.0.1.{i}", 50000)) for i in range(4)]
    assert [_run(clients[0]), _prove(clients[1]), _run(clients[2]), _prove(clients[3])] == [
        400, 404, 429, 429,
    ]
    monkeypatch.setattr(auth, "_today", lambda: "2026-10-20")
    assert _run(clients[3]) == 400


def test_a_visitor_still_cannot_upload(heavy_open: dict[str, float]) -> None:
    client = TestClient(api_main.create_app())
    files = {"file": ("scan.zip", b"PK", "application/zip")}
    data = {"acknowledged": "true", "preset": "bone"}
    assert client.post("/demo/runs/upload", files=files, data=data).status_code == 401
