"""
Tests for webui/app.py. Uses FastAPI's TestClient (in-process, real ASGI
lifespan) -- app startup genuinely spawns the pfmea-consolidation MCP
server subprocess (mcp_server/server.py) via run_agent.build_mcp_server(),
same as run_agent.py itself, so these tests exercise the real wiring, not
a mock. One client is shared across the module (module-scoped fixture) so
that subprocess only starts/stops once for the whole file.

The full real-agent chat round trip is gated behind OPENAI_API_KEY, same
pattern as the DATABASE_URL-gated tests elsewhere in this repo.
"""
from __future__ import annotations

import base64
import json
import os

import pytest
from fastapi.testclient import TestClient

from webui.app import app

# Same genuine PNG literal as tests/test_run_agent.py -- see that file's
# comment for how it was built and verified.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR42mP4z8AARAwQ"
    "CgAf7gP9Y167WwAAAABJRU5ErkJggg=="
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _read_ndjson(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        if not line.strip():
            continue
        events.append(json.loads(line))
    return events


def test_index_serves_the_chat_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "PFMEA Consolidation Agent" in resp.text
    assert "/api/chat" in resp.text


def test_health_reports_ready_when_key_is_present(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-for-this-test")
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ready": True}


def test_health_reports_not_ready_when_key_is_missing(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is False
    assert "OPENAI_API_KEY" in body["message"]


def test_chat_without_key_streams_a_clean_error_not_a_crash(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with client.stream("POST", "/api/chat", json={"message": "hello", "history": []}) as resp:
        assert resp.status_code == 200
        events = _read_ndjson(resp)
    assert events[0]["type"] == "error"
    assert "OPENAI_API_KEY" in events[0]["message"]


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set — needs a real agent call")
def test_chat_streams_a_real_response_and_returns_history(client):
    with client.stream(
        "POST", "/api/chat",
        json={"message": "In one short sentence, what does get_pipeline_status do?", "history": []},
    ) as resp:
        assert resp.status_code == 200
        events = _read_ndjson(resp)

    assert not any(e["type"] == "error" for e in events)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas, "expected at least one text delta from a real streamed response"
    full_text = "".join(deltas)
    assert full_text.strip()

    history_events = [e for e in events if e["type"] == "history"]
    assert len(history_events) == 1
    items = history_events[0]["items"]
    assert len(items) >= 2  # at least the user message and the assistant's reply
    assert items[0]["content"] == "In one short sentence, what does get_pipeline_status do?"


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set — needs a real agent call")
def test_chat_with_an_attached_image_streams_a_real_response(client):
    # GENERATION intake's "upload a drawing" path: the browser sends the
    # attached file as a data URL in the `image` field.
    data_url = f"data:image/png;base64,{_TINY_PNG_B64}"
    with client.stream(
        "POST", "/api/chat",
        json={"message": "In one short sentence, what color is this image?", "history": [], "image": data_url},
    ) as resp:
        assert resp.status_code == 200
        events = _read_ndjson(resp)

    assert not any(e["type"] == "error" for e in events)
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert deltas
    full_text = "".join(deltas)
    assert full_text.strip()

    history_events = [e for e in events if e["type"] == "history"]
    assert len(history_events) == 1
    items = history_events[0]["items"]
    # The round-tripped history stores the multimodal content list, not a
    # plain string, for the turn that carried an image.
    user_item = items[0]
    assert isinstance(user_item["content"], list)
    types = {part["type"] for part in user_item["content"]}
    assert types == {"input_text", "input_image"}


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set — needs a real agent call")
def test_chat_conversation_history_round_trips_across_turns(client):
    with client.stream(
        "POST", "/api/chat",
        json={"message": "Remember the number 42. Reply with just 'ok'.", "history": []},
    ) as resp:
        first_events = _read_ndjson(resp)
    history = next(e["items"] for e in first_events if e["type"] == "history")

    with client.stream(
        "POST", "/api/chat",
        json={"message": "What number did I just ask you to remember? Reply with just the number.", "history": history},
    ) as resp:
        second_events = _read_ndjson(resp)

    second_text = "".join(e["text"] for e in second_events if e["type"] == "delta")
    assert "42" in second_text
