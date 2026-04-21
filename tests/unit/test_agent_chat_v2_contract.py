from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent.core.loop import (
    ImageBlock,
    Message,
    ReasoningDelta,
    TextDelta,
    ToolUseDelta,
    TurnEnd,
)
from agent.ui.server import create_app


class FakeResponsesAdapter:
    instances: list["FakeResponsesAdapter"] = []

    def __init__(self, model: str, api_key: str, base_url: str | None = None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.messages: list[Message] = []
        self.tools: list[dict] = []
        self.system: str | None = None
        FakeResponsesAdapter.instances.append(self)

    async def stream(self, messages, tools, system=None, **options):
        self.messages = list(messages)
        self.tools = list(tools or [])
        self.system = system
        yield ReasoningDelta(text="checking metadata")
        yield TextDelta(text="hello")
        yield TurnEnd(stop_reason="end_turn", usage={"total_tokens": 3})


class FakeMemoryManager:
    def __init__(self, context: str = ""):
        self.context = context
        self.calls: list[dict] = []

    def get_context_injection(self, conv_id=None, **kwargs):
        self.calls.append({"conv_id": conv_id, **kwargs})
        return self.context

    def get_facts(self, category=None, limit=100):
        return []

    def delete_fact(self, fact_id: int):
        return False


class FakeToolCallAdapter(FakeResponsesAdapter):
    async def stream(self, messages, tools, system=None, **options):
        self.messages = list(messages)
        self.tools = list(tools or [])
        self.system = system
        if not hasattr(self, "calls"):
            self.calls = 0
        self.calls += 1
        if self.calls == 1:
            yield ToolUseDelta(
                id="call-bash",
                name="Bash",
                input_partial={"command": "echo should-not-run"},
            )
            yield TurnEnd(stop_reason="tool_use", usage={"total_tokens": 1})
            return
        yield TextDelta(text="I could not run Bash in read-only mode.")
        yield TurnEnd(stop_reason="end_turn", usage={"total_tokens": 1})


def _write_config(tmp_path, *, provider_type: str = "openai", model: str = "gpt-5.4"):
    (tmp_path / "app.yaml").write_text(
        """
active_profile: test
profiles:
  test: {}
active_kbs:
  - kb-a
knowledge_bases: []
runtime:
  mode: inline
  monitor:
    enabled: false
    wake_on_task_complete: false
    heartbeat_seconds: 30
agent:
  compaction:
    enabled: true
    token_threshold: 100
    trigger_ratio: 0.5
    protected_recent_messages: 4
    protected_recent_tokens: 120
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "models.yaml").write_text(
        f"""
profiles:
  test:
    llm:
      active: provider
      providers:
        provider:
          type: {provider_type}
          model: {model}
          api_key_ref: test.llm.provider
""".strip(),
        encoding="utf-8",
    )


@pytest.fixture
def v2_client(tmp_path, monkeypatch):
    _write_config(tmp_path)
    FakeResponsesAdapter.instances.clear()

    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        FakeResponsesAdapter,
    )

    return TestClient(create_app(str(tmp_path)))


def _make_v2_client(tmp_path, monkeypatch, *, memory_context: str = ""):
    _write_config(tmp_path)
    FakeResponsesAdapter.instances.clear()
    memory = FakeMemoryManager(memory_context)

    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr("agent.ui.server.get_memory_manager", lambda: memory)
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        FakeResponsesAdapter,
    )

    return TestClient(create_app(str(tmp_path))), memory


def _events(text: str) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        event = ""
        data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event and data:
            parsed.append((event, json.loads(data)))
    return parsed


def test_v2_runtime_reports_single_agentloop_path(v2_client):
    res = v2_client.get("/api/agent_runtime")

    assert res.status_code == 200
    body = res.json()
    assert body["chat_endpoint"] == "/api/agent_chat_v2"
    assert body["chat_executor"] == "AgentLoop"
    assert body["ui_uses_agent_loop"] is True


def test_v2_streams_deltas_and_injects_session_metadata(v2_client):
    res = v2_client.post(
        "/api/agent_chat_v2",
        json={
            "message": "What model are you?",
            "history": [{"role": "assistant", "content": "previous answer"}],
            "conversation_id": "conv-1",
            "max_iterations": 1,
        },
    )

    assert res.status_code == 200
    events = _events(res.text)
    activity = [payload for event, payload in events if event == "activity"]
    tokens = [payload["text"] for event, payload in events if event == "token"]
    done = [payload for event, payload in events if event == "done"]

    assert any(item["type"] == "agent_start" for item in activity)
    assert any(item["type"] == "tool_manifest" for item in activity)
    assert any(item["type"] == "thinking_update" for item in activity)
    assert tokens == ["hello"]
    assert done[-1]["model"] == "gpt-5.4"

    adapter = FakeResponsesAdapter.instances[-1]
    assert adapter.system is not None
    assert "<session_metadata>" in adapter.system
    assert '"conversation_id": "conv-1"' in adapter.system
    assert '"model": "gpt-5.4"' in adapter.system
    assert '"active_kbs": [\n    "kb-a"\n  ]' in adapter.system
    assert adapter.messages[-1].content[0].text == "What model are you?"


def test_v2_accepts_image_payload_and_announces_it(v2_client):
    res = v2_client.post(
        "/api/agent_chat_v2",
        json={
            "message": "describe this",
            "images": [
                {
                    "base64": "aGVsbG8=",
                    "media_type": "image/png",
                    "name": "pixel.png",
                }
            ],
            "max_iterations": 1,
        },
    )

    assert res.status_code == 200
    events = _events(res.text)
    assert any(
        event == "activity" and payload["type"] == "input_images"
        for event, payload in events
    )

    adapter = FakeResponsesAdapter.instances[-1]
    user_blocks = adapter.messages[-1].content
    image = next(block for block in user_blocks if isinstance(block, ImageBlock))
    assert image.base64 == "aGVsbG8="
    assert image.media_type == "image/png"
    assert image.name == "pixel.png"


def test_v2_accepts_non_openai_provider_via_adapter_factory(tmp_path, monkeypatch):
    _write_config(tmp_path, provider_type="gemini", model="gemini-1.5-pro")
    created: list[dict] = []

    def fake_factory(provider_type, model_name, api_key, base_url=None):
        created.append({
            "provider_type": provider_type,
            "model_name": model_name,
            "api_key": api_key,
            "base_url": base_url,
        })
        return FakeResponsesAdapter(model=model_name, api_key=api_key, base_url=base_url)

    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr("agent.ui.server._create_agent_loop_adapter", fake_factory)
    client = TestClient(create_app(str(tmp_path)))

    res = client.post("/api/agent_chat_v2", json={"message": "hello"})

    assert res.status_code == 200
    assert created[-1]["provider_type"] == "gemini"
    assert created[-1]["model_name"] == "gemini-1.5-pro"
    assert _events(res.text)[-1][1]["provider"] == "provider"


def test_v2_read_mode_blocks_needs_approval_tools(tmp_path, monkeypatch):
    _write_config(tmp_path)
    FakeResponsesAdapter.instances.clear()

    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        FakeToolCallAdapter,
    )
    client = TestClient(create_app(str(tmp_path)))

    res = client.post(
        "/api/agent_chat_v2",
        json={"message": "run bash", "mode": "read", "max_iterations": 2},
    )

    assert res.status_code == 200
    events = _events(res.text)
    tool_results = [
        payload
        for event, payload in events
        if event == "activity" and payload.get("type") == "tool_result"
    ]
    assert tool_results
    assert tool_results[0]["status"] == "error"
    assert "blocked in plan mode" in tool_results[0]["detail"]


def test_v2_should_compact_large_history_before_model_call(v2_client):
    history = [
        {"role": "user", "content": f"old user message {i}"}
        for i in range(120)
    ]

    v2_client.post(
        "/api/agent_chat_v2",
        json={"message": "latest", "history": history, "max_iterations": 1},
    )

    adapter = FakeResponsesAdapter.instances[-1]
    assert len(adapter.messages) < len(history) + 1


def test_v2_injects_user_facts_into_system_prompt(tmp_path, monkeypatch):
    client, memory = _make_v2_client(
        tmp_path,
        monkeypatch,
        memory_context="## user_facts\n- User prefers concise Chinese answers.",
    )

    client.post(
        "/api/agent_chat_v2",
        json={
            "message": "remembered facts?",
            "conversation_id": "conv-memory",
            "max_iterations": 1,
        },
    )

    adapter = FakeResponsesAdapter.instances[-1]
    assert "## user_facts" in (adapter.system or "")
    assert "User prefers concise Chinese answers." in (adapter.system or "")
    assert memory.calls[-1]["conv_id"] == "conv-memory"
