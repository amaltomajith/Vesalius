"""The optional AI endpoints (src/api/routers/ai.py): off without a key; a patient study's prompt
is built only from allow-listed measurement fields and can never carry an image; the atlas route
only accepts atlas structures. The upstream model is replaced by a recorder: nothing leaves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.dependencies import serving_session_dependency
from api.routers import ai

MANIFEST = {
    "deface": {"required": True, "applied": True, "structures_cut": "s7_region_v1"},
    "study": {
        "modality": "CT",
        "body_part": "HEAD",
        "voxel_spacing_mm": [1, 1, 1],
        "pseudo_patient_id": "PLANTED-PID",
    },
    "series_id": "PLANTED-SERIES",
    "groups": [
        {
            "id": "nervous",
            "label": "Nervous",
            "structures": [
                {
                    "node": "brain",
                    "label": "Brain",
                    "volume_ml": 1234.5,
                    "hu_mean": 33.1,
                    "hu_std": 9.0,
                    "bbox_mm": [[0, 0, 0], [140, 170, 120]],
                    "note": "PLANTED-EXTRA",
                },
            ],
        }
    ],
}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Records every payload that would go upstream, and answers with a fixed stream."""
    calls: list[dict[str, Any]] = []

    def fake_open(key: str, payload: dict[str, Any]) -> Iterator[str]:
        calls.append(payload)
        return iter(["The volume ", "is typical."])

    monkeypatch.setattr(ai, "open_stream", fake_open)
    monkeypatch.setattr(
        ai, "_gold_manifest", lambda session, settings, sid: json.loads(json.dumps(MANIFEST))
    )
    monkeypatch.setattr(ai, "_atlas_nodes", lambda: frozenset({"Femurl"}))
    return calls


@pytest.fixture
def client() -> TestClient:
    app = api_main.create_app()
    app.dependency_overrides[serving_session_dependency] = lambda: None
    return TestClient(app)


def test_off_without_a_key(
    client: TestClient, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert client.get("/ai/status").json() == {"enabled": False, "vision": False}
    r = client.post("/ai/explain/study", json={"study_id": str(uuid4()), "node": "brain"})
    assert r.status_code == 503 and "off" in r.json()["detail"]
    assert (
        client.post("/ai/explain/atlas", json={"node": "Femurl", "label": "Femur"}).status_code
        == 503
    )
    assert sent == []


def test_a_study_prompt_holds_measurements_and_nothing_else(
    client: TestClient, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    r = client.post("/ai/explain/study", json={"study_id": str(uuid4()), "node": "brain"})
    assert r.status_code == 200 and r.text == "The volume is typical."
    wire = json.dumps(sent[0])
    assert "1234.5" in wire and "33.1" in wire and "CT" in wire
    for planted in ("PLANTED-PID", "PLANTED-SERIES", "PLANTED-EXTRA", "test-key"):
        assert planted not in wire, planted
    assert "image_url" not in wire
    assert "not diagnosis" in wire


def test_a_study_request_cannot_carry_an_image(
    client: TestClient, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    r = client.post(
        "/ai/explain/study",
        json={"study_id": str(uuid4()), "node": "brain", "image": "data:image/png;base64,AAAA"},
    )
    assert r.status_code == 422 and sent == []
    assert (
        client.post(
            "/ai/explain/study", json={"study_id": str(uuid4()), "node": "../etc"}
        ).status_code
        == 422
    )


def test_an_unknown_structure_is_refused(
    client: TestClient, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    assert (
        client.post(
            "/ai/explain/study", json={"study_id": str(uuid4()), "node": "liver"}
        ).status_code
        == 404
    )
    assert (
        client.post("/ai/explain/atlas", json={"node": "brain", "label": "Brain"}).status_code
        == 404
    )
    assert sent == []


def test_the_atlas_may_send_its_snapshot(
    client: TestClient, sent: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    assert (
        client.post("/ai/explain/atlas", json={"node": "Femurl", "label": "Femur"}).status_code
        == 200
    )
    assert "image_url" not in json.dumps(sent[-1])
    image = "data:image/png;base64,iVBORw0KGgo="
    assert (
        client.post(
            "/ai/explain/atlas", json={"node": "Femurl", "label": "Femur", "image": image}
        ).status_code
        == 200
    )
    assert sent[-1]["model"] == ai.VISION_MODEL and image in json.dumps(sent[-1])
    bad = client.post(
        "/ai/explain/atlas",
        json={"node": "Femurl", "label": "Femur", "image": "javascript:alert(1)"},
    )
    assert bad.status_code == 422


def test_requests_to_groq_name_the_app_not_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Groq's Cloudflare edge answers Python's default urllib user agent with a 403
    (error code 1010): the request must carry the app's own User-Agent."""
    seen: list[Any] = []

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def __iter__(self) -> Iterator[bytes]:
            return iter([b'data: {"choices":[{"delta":{"content":"ok"}}]}', b"data: [DONE]"])

    def fake_urlopen(req: Any, timeout: float) -> FakeResponse:
        seen.append(req)
        return FakeResponse()

    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
    text = "".join(ai.open_stream("test-key", {"model": "m", "messages": []}))
    assert text == "ok"
    agent = seen[0].get_header("User-agent")
    assert agent == ai.USER_AGENT
    assert not agent.lower().startswith("python")


def test_gpt_oss_reasons_briefly_so_the_answer_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    """gpt-oss's thinking counts against max_tokens; a long prompt was cut off mid-sentence."""
    bodies: list[dict[str, Any]] = []

    class Empty:
        def __enter__(self) -> Empty:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def __iter__(self) -> Iterator[bytes]:
            return iter([])

    def fake_urlopen(req: Any, timeout: float) -> Empty:
        bodies.append(json.loads(req.data))
        return Empty()

    monkeypatch.setattr(ai.urllib.request, "urlopen", fake_urlopen)
    list(ai.open_stream("k", {"model": "openai/gpt-oss-120b", "messages": []}))
    list(ai.open_stream("k", {"model": "qwen/qwen3.8-27b", "messages": []}))
    assert bodies[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in bodies[1]

