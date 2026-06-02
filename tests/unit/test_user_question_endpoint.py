"""HTTP-level smoke for AskUserQuestion + /api/user_questions/{id} (P12.3)."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from agent.core.loop import (
    Message,
    ReasoningDelta,
    TextDelta,
    ToolUseDelta,
    TurnEnd,
)
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


class _RecordingAdapter:
    """Adapter that records its system prompt and answers in one turn."""

    instances: list["_RecordingAdapter"] = []

    def __init__(self, model, api_key, base_url=None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.system: str | None = None
        self.tools_seen: list[str] = []
        _RecordingAdapter.instances.append(self)

    async def stream(self, messages, tools, system=None, **options):
        self.system = system
        self.tools_seen = [t["name"] for t in (tools or [])]
        yield TextDelta(text="ok")
        yield TurnEnd(stop_reason="end_turn", usage={"total_tokens": 1})


@pytest.fixture
def client(tmp_path, monkeypatch):
    _write_config(tmp_path)
    _RecordingAdapter.instances.clear()
    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        _RecordingAdapter,
    )
    return TestClient(create_app(str(tmp_path)))


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


def test_ask_tool_is_exposed_in_v2_turn(client):
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Help me with a doc",
            "conversation_id": "conv-ask-1",
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    events = _events(res.text)
    manifest = [
        p for e, p in events if e == "activity" and p["type"] == "tool_manifest"
    ]
    assert manifest, events
    tool_names = manifest[0]["meta"]["tools"]
    assert "AskUserQuestion" in tool_names


def test_clarification_policy_appears_in_system_prompt(client):
    client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Make me a presentation about Q4 results",
            "conversation_id": "conv-ask-2",
            "max_iterations": 1,
        },
    )
    adapter = _RecordingAdapter.instances[-1]
    assert adapter.system is not None
    # The policy block is injected when AskUserQuestion is in the tool set.
    assert "<clarification_policy>" in adapter.system
    assert "AskUserQuestion" in adapter.system
    assert "ambiguous" in adapter.system.lower()


def test_answer_endpoint_resolves_pending_future(client):
    """Register a future directly in app.state, hit the endpoint, verify resolve."""
    pending = client.app.state.pending_user_questions
    # We need an asyncio loop running to create a Future. TestClient's helper.
    fut_box: dict = {}
    answered_box: dict = {}

    async def _setup():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        pending["qid-test-1"] = {"future": fut, "question": "test?"}
        fut_box["fut"] = fut
        try:
            answered_box["reply"] = await asyncio.wait_for(fut, timeout=5.0)
        except asyncio.TimeoutError:
            answered_box["reply"] = "timeout"

    async def _drive():
        task = asyncio.create_task(_setup())
        # Yield so the future is registered before we hit the endpoint.
        await asyncio.sleep(0.05)
        # Hit the endpoint from within the same loop via the test client.
        # TestClient is sync, so call it in a thread.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            fut2 = pool.submit(
                client.post,
                "/api/user_questions/qid-test-1",
                json={"answer": "yes", "selected_option": "yes"},
            )
            res = await asyncio.get_running_loop().run_in_executor(
                None, fut2.result
            )
        await task
        return res

    res = asyncio.run(_drive())
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert answered_box["reply"] == {
        "answer": "yes",
        "selected_option": "yes",
        "selected_options": None,
    }


def test_answer_endpoint_returns_404_for_unknown_question(client):
    res = client.post(
        "/api/user_questions/no-such-id",
        json={"answer": "x"},
    )
    assert res.status_code == 404


def test_answer_endpoint_idempotent_when_future_already_resolved(client):
    pending = client.app.state.pending_user_questions

    async def _setup_and_resolve():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        pending["qid-test-2"] = {"future": fut, "question": "test?"}
        fut.set_result({"answer": "pre"})
        # No await; future already done. Endpoint should not crash.

    asyncio.run(_setup_and_resolve())
    res = client.post(
        "/api/user_questions/qid-test-2",
        json={"answer": "late"},
    )
    # Endpoint returns 200 even though the future was already resolved.
    assert res.status_code == 200
