"""/health/ready and /health/idle: what the on-demand live backend is woken and put to sleep by.

The wake function waits for /health/ready (database and object store answer), and autostop.sh
deallocates the VM once /health/idle has seen no real request for a while. Health checks themselves
(uptime pings, the wake function's polling) must never count as someone using it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import demo_state
from api import main as api_main
from api.routers import health


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(health, "_database_ok", lambda: True)
    monkeypatch.setattr(health, "_store_ok", lambda: True)
    return TestClient(api_main.create_app())


def test_ready_once_the_database_and_the_store_answer(client: TestClient) -> None:
    r = client.get("/health/ready")
    assert (r.status_code, r.json()) == (200, {"status": "ready"})


def _broken() -> bool:
    raise ConnectionError("not up yet")


@pytest.mark.parametrize(
    ("part", "check"), [("database", "_database_ok"), ("object_store", "_store_ok")]
)
def test_not_ready_says_what_it_is_waiting_for(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, part: str, check: str
) -> None:
    monkeypatch.setattr(health, check, _broken)
    r = client.get("/health/ready")
    assert (r.status_code, r.json()["waiting_for"]) == (503, part)
    monkeypatch.setattr(health, check, lambda: False)
    assert client.get("/health/ready").status_code == 503


def test_idle_counts_only_real_requests(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"t": 5000.0}
    monkeypatch.setattr(health, "_clock", lambda: clock["t"])
    client.get("/")  # a visitor
    clock["t"] += 600
    for path in ("/health", "/health/ready", "/health/idle"):  # pings and polling
        client.get(path)
    assert client.get("/health/idle").json() == {"idle_seconds": 600.0, "busy": False}
    client.get("/app/")
    assert client.get("/health/idle").json()["idle_seconds"] == 0.0


def test_idle_reports_a_running_job_as_busy(client: TestClient) -> None:
    with demo_state.HEAVY_LOCK:
        assert client.get("/health/idle").json()["busy"] is True
    assert client.get("/health/idle").json()["busy"] is False
