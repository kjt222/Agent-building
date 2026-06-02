"""Backend wiring for P12.4 Plan Mode.

Covers:
- Payload `plan_mode: true` flips LoopConfig.permission_mode to "plan",
  independent of access_mode.
- The `<plan_mode>` system-prompt block is present when enabled, absent
  otherwise.
- SSE `done` event carries `plan_mode_used`.
- The loop-level gate (already implemented in agent/core/loop.py) does
  reject a mutating tool when permission_mode="plan".
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agent.core.loop import TextDelta, ToolUseDelta, TurnEnd
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


class _CapturingAdapter:
    """Adapter that records the system prompt + lets the test trigger a tool call."""

    last_system: str | None = None
    next_tool_call: dict | None = None

    def __init__(self, model, api_key, base_url=None):
        self.model = model

    async def stream(self, messages, tools, system=None, **options):
        type(self).last_system = system or ""
        call = type(self).next_tool_call
        type(self).next_tool_call = None
        if call:
            yield ToolUseDelta(
                id=call.get("id", "call-1"),
                name=call["name"],
                input_partial=call.get("input") or {},
            )
            yield TurnEnd(
                stop_reason="tool_use",
                usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            )
            return
        yield TextDelta(text="plan ready.")
        yield TurnEnd(
            stop_reason="end_turn",
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )


@pytest.fixture
def client(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        _CapturingAdapter,
    )
    _CapturingAdapter.last_system = None
    _CapturingAdapter.next_tool_call = None
    yield TestClient(create_app(str(tmp_path)))


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
            try:
                parsed.append((event, json.loads(data)))
            except json.JSONDecodeError:
                parsed.append((event, {"raw": data}))
    return parsed


def test_plan_mode_true_injects_system_block(client):
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Refactor the storage layer.",
            "conversation_id": "conv-plan-1",
            "plan_mode": True,
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    sys_prompt = _CapturingAdapter.last_system or ""
    assert "<plan_mode>" in sys_prompt
    assert "Plan mode is ACTIVE" in sys_prompt
    assert "</plan_mode>" in sys_prompt


def test_plan_mode_false_no_system_block(client):
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Hi there.",
            "conversation_id": "conv-plan-2",
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    sys_prompt = _CapturingAdapter.last_system or ""
    assert "<plan_mode>" not in sys_prompt


def test_done_event_reports_plan_mode_used(client):
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Plan a refactor.",
            "conversation_id": "conv-plan-3",
            "plan_mode": True,
            "max_iterations": 1,
        },
    )
    done = [p for e, p in _events(res.text) if e == "done"]
    assert done, "expected a done event"
    assert done[-1].get("plan_mode_used") is True


def test_done_event_plan_mode_used_false_by_default(client):
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Hi.",
            "conversation_id": "conv-plan-4",
            "max_iterations": 1,
        },
    )
    done = [p for e, p in _events(res.text) if e == "done"]
    assert done[-1].get("plan_mode_used") is False


@pytest.mark.asyncio
async def test_loop_plan_gate_blocks_mutation_tool():
    """When LoopConfig.permission_mode='plan', a non-SAFE tool is rejected
    by the loop's plan gate before its run() is called."""
    from agent.core.loop import (
        AgentLoop,
        LoopConfig,
        Message,
        PermissionLevel,
        ReasoningDelta,
        Role,
        TextBlock,
        TextDelta,
        ToolResultBlock,
        ToolUseBlock,
        ToolUseDelta,
        TurnEnd,
    )

    class _MutatingTool:
        name = "FakeWriter"
        description = "would write a file"
        input_schema = {"type": "object"}
        permission_level = PermissionLevel.NEEDS_APPROVAL
        parallel_safe = False
        called = False

        async def run(self, inp, ctx):
            type(self).called = True
            return ToolResultBlock(tool_use_id="", content="wrote.")

    tool = _MutatingTool()

    class _Adapter:
        def __init__(self):
            self.calls = 0

        async def stream(self, messages, tools, system=None, **options):
            self.calls += 1
            if self.calls == 1:
                yield ToolUseDelta(id="t1", name="FakeWriter", input_partial={})
                yield TurnEnd(stop_reason="tool_use", usage={"total_tokens": 1})
                return
            yield TextDelta(text="ok")
            yield TurnEnd(stop_reason="end_turn", usage={"total_tokens": 1})

    loop = AgentLoop(
        adapter=_Adapter(),
        tools={"FakeWriter": tool},
        config=LoopConfig(permission_mode="plan", max_iterations=3),
    )
    results: list = []
    async for event in loop.run("plan something", history=[]):
        results.append(event)
    # The mutating tool's run() must NOT have executed.
    assert tool.called is False
    # The loop should have produced a tool_result block carrying the
    # blocked-in-plan-mode error.
    final_messages = [r for r in results if isinstance(r, Message)]
    assert final_messages, "expected at least one Message in the trace"
    blocked_text = ""
    for msg in final_messages:
        for block in msg.content:
            if isinstance(block, ToolResultBlock) and block.is_error:
                blocked_text += str(block.content or "")
    assert "plan mode" in blocked_text.lower(), blocked_text


@pytest.mark.asyncio
async def test_loop_default_mode_allows_mutation_tool():
    """Without plan mode, the same tool runs normally."""
    from agent.core.loop import (
        AgentLoop,
        LoopConfig,
        Message,
        PermissionLevel,
        TextDelta,
        ToolResultBlock,
        ToolUseDelta,
        TurnEnd,
    )

    class _MutatingTool:
        name = "FakeWriter"
        description = "would write a file"
        input_schema = {"type": "object"}
        permission_level = PermissionLevel.NEEDS_APPROVAL
        parallel_safe = False
        called = False

        async def run(self, inp, ctx):
            type(self).called = True
            return ToolResultBlock(tool_use_id="", content="wrote.")

    tool = _MutatingTool()

    class _Adapter:
        def __init__(self):
            self.calls = 0

        async def stream(self, messages, tools, system=None, **options):
            self.calls += 1
            if self.calls == 1:
                yield ToolUseDelta(id="t1", name="FakeWriter", input_partial={})
                yield TurnEnd(stop_reason="tool_use", usage={"total_tokens": 1})
                return
            yield TextDelta(text="ok")
            yield TurnEnd(stop_reason="end_turn", usage={"total_tokens": 1})

    loop = AgentLoop(
        adapter=_Adapter(),
        tools={"FakeWriter": tool},
        config=LoopConfig(permission_mode="default", max_iterations=3),
    )
    async for _event in loop.run("do it", history=[]):
        pass
    assert tool.called is True
