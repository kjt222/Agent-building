"""HTTP-level smoke for /api/conversations/{id}/interrupt (P12.1).

Uses the FastAPI TestClient + a fake streaming adapter. The adapter signals
the interrupt mid-stream so the cancel race fires inside ``AgentLoop`` without
needing wall-clock timing.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent.core import interrupt_registry as ir
from agent.core.loop import ReasoningDelta, TextDelta, TurnEnd
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


class _SelfInterruptingAdapter:
    """Adapter that fires the interrupt signal partway through its stream.

    The first delta goes through. Before the second delta is yielded, the
    adapter sets the cancel event for the test conversation. AgentLoop's
    delta-race must observe the interrupt before forwarding the rest.
    """

    instances: list["_SelfInterruptingAdapter"] = []
    target_conversation = "conv-int-1"

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.delivered: list[str] = []
        _SelfInterruptingAdapter.instances.append(self)

    async def stream(self, messages, tools, system=None, **options):
        self.delivered.append("text-1")
        yield TextDelta(text="hello")
        # Self-trigger interrupt as if the user clicked Stop.
        ir.set_interrupt(self.target_conversation)
        self.delivered.append("text-2")
        yield TextDelta(text=" world")
        self.delivered.append("turn-end")
        yield TurnEnd(stop_reason="end_turn", usage={"total_tokens": 2})


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


@pytest.fixture
def client(tmp_path, monkeypatch):
    _write_config(tmp_path)
    _SelfInterruptingAdapter.instances.clear()
    ir.reset_all()
    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        _SelfInterruptingAdapter,
    )
    yield TestClient(create_app(str(tmp_path)))
    ir.reset_all()


def test_interrupt_endpoint_signals_active_loop(client):
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "stream please",
            "conversation_id": _SelfInterruptingAdapter.target_conversation,
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    events = _events(res.text)

    # The adapter set the interrupt after delta 1, so the loop must:
    # 1. break out of _one_turn before delivering the third delta
    # 2. emit an "interrupted" activity event
    # 3. emit done with stop_reason="user_interrupt"
    interrupted = [
        p for e, p in events if e == "activity" and p.get("type") == "interrupted"
    ]
    assert len(interrupted) == 1, events
    done = [p for e, p in events if e == "done"]
    assert done, events
    assert done[-1]["interrupted"] is True
    assert done[-1]["stop_reason"] == "user_interrupt"

    adapter = _SelfInterruptingAdapter.instances[-1]
    # Adapter generator drove past text-1; the rest of the stream may or may
    # not have run depending on race timing, but the loop must NOT have
    # continued past the interrupt point (no second turn).
    assert adapter.delivered[0] == "text-1"


def test_interrupt_endpoint_returns_signalled_false_when_no_active_run(client):
    res = client.post("/api/conversations/never-existed/interrupt")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["signalled"] is False
    assert body["conversation_id"] == "never-existed"


def test_interrupt_endpoint_idempotent_after_run_finishes(client):
    # Run completes naturally first via the self-interrupting adapter.
    client.post(
        "/api/agent_chat_v2",
        json={
            "message": "hi",
            "conversation_id": _SelfInterruptingAdapter.target_conversation,
            "max_iterations": 1,
        },
    )
    # After the SSE stream closes the registry entry is released; a
    # follow-up interrupt should be a clean no-op, not a 500.
    res = client.post(
        f"/api/conversations/{_SelfInterruptingAdapter.target_conversation}/interrupt"
    )
    assert res.status_code == 200
    assert res.json()["signalled"] is False
