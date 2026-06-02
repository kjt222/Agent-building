"""HTTP-level smoke for usage SSE event + GET endpoint (P12.6)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent.core import usage_registry as ur
from agent.core.loop import TextDelta, TurnEnd
from agent.ui.server import create_app


def _write_config(tmp_path):
    (tmp_path / "app.yaml").write_text(
        """
active_profile: test
profiles:
  test: {}
active_kbs: []
knowledge_bases: []
runtime:
  mode: inline
  monitor:
    enabled: false
    wake_on_task_complete: false
    heartbeat_seconds: 30
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "models.yaml").write_text(
        """
profiles:
  test:
    llm:
      active: provider
      providers:
        provider:
          type: openai
          model: gpt-5.4
          api_key_ref: test.llm.provider
""".strip(),
        encoding="utf-8",
    )


class _UsageAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    async def stream(self, messages, tools, system=None, **options):
        yield TextDelta(text="ok")
        yield TurnEnd(
            stop_reason="end_turn",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "reasoning_tokens": 10,
                "total_tokens": 150,
            },
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    _write_config(tmp_path)
    ur.reset_all()
    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        _UsageAdapter,
    )
    yield TestClient(create_app(str(tmp_path)))
    ur.reset_all()


def _events(text: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        event, data = "", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event and data:
            parsed.append((event, json.loads(data)))
    return parsed


def test_v2_emits_usage_update_activity(client):
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "hi",
            "conversation_id": "conv-usage-1",
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    usage_events = [
        p for e, p in _events(res.text)
        if e == "activity" and p["type"] == "usage_update"
    ]
    assert len(usage_events) == 1
    meta = usage_events[0]["meta"]
    assert meta["run"]["input_tokens"] == 100
    assert meta["run"]["output_tokens"] == 50
    assert meta["run"]["total_tokens"] == 150
    assert meta["cumulative"]["total_tokens"] == 150
    assert meta["model"] == "gpt-5.4"
    # gpt-5.4 is priced; cost should be a float.
    assert isinstance(meta["run"]["cost_usd"], float)


def test_usage_accumulates_across_runs(client):
    for _ in range(3):
        client.post(
            "/api/agent_chat_v2",
            json={
                "message": "hi",
                "conversation_id": "conv-usage-2",
                "max_iterations": 1,
            },
        )
    res = client.get("/api/conversations/conv-usage-2/usage")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["cumulative"]["total_tokens"] == 450
    assert body["cumulative"]["input_tokens"] == 300


def test_usage_endpoint_returns_zero_for_fresh_conversation(client):
    res = client.get("/api/conversations/never-used/usage")
    assert res.status_code == 200
    assert res.json()["cumulative"]["total_tokens"] == 0


def test_delete_conversation_clears_usage(client):
    # Create + use a conversation, then delete it; cumulative resets.
    create_res = client.post("/api/conversations", json={})
    conv_id = create_res.json()["conversation_id"]
    client.post(
        "/api/agent_chat_v2",
        json={"message": "hi", "conversation_id": conv_id, "max_iterations": 1},
    )
    assert ur.get_cumulative(conv_id)["total_tokens"] == 150
    client.delete(f"/api/conversations/{conv_id}")
    assert ur.get_cumulative(conv_id)["total_tokens"] == 0
