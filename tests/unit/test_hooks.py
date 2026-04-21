"""Tests for approval, intent, and final delivery hooks."""

from __future__ import annotations

import asyncio

from agent.core.hooks import (
    detect_intent_without_action,
    make_approval_hook,
    make_final_guard_hook,
    make_intent_without_action_hook,
)
from agent.core.loop import (
    AgentLoop,
    Hooks,
    LoopContext,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    ToolResultBlock,
)
from agent.tools_v2.primitives import WriteTool as PrimitiveWriteTool

from tests.unit.mock_adapter import MockAdapter, text_turn, tool_turn


class SafeRead:
    name = "read"
    description = "read"
    input_schema = {"type": "object"}
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input, ctx):
        return ToolResultBlock(tool_use_id="", content="read-ok")


class DangerousWrite:
    name = "write"
    description = "write"
    input_schema = {"type": "object"}
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input, ctx):
        return ToolResultBlock(tool_use_id="", content="wrote")


async def _drive(loop: AgentLoop, msg: str) -> list:
    out = []
    async for ev in loop.run(msg):
        out.append(ev)
    return out


def _tool_results(events) -> list[ToolResultBlock]:
    return [
        b
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
        for b in e.content
        if isinstance(b, ToolResultBlock)
    ]


def _text_user_messages(events) -> list[str]:
    return [
        "".join(b.text for b in e.content if isinstance(b, TextBlock))
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
    ]


def test_approval_hook_denies_needs_approval_tool():
    adp = MockAdapter([
        tool_turn("c1", "write", {"path": "x"}),
        text_turn("ok"),
    ])
    tools = {"read": SafeRead(), "write": DangerousWrite()}

    async def deny(_use, _ctx):
        return False

    hooks = Hooks(pre_tool_use=[make_approval_hook(tools, deny)])
    loop = AgentLoop(adapter=adp, tools=tools, hooks=hooks)
    events = asyncio.run(_drive(loop, "go"))
    results = _tool_results(events)
    assert results[0].is_error is True
    assert "denied" in results[0].content.lower()
    assert results[0].tool_use_id == "c1"


def test_approval_hook_allows_when_approver_returns_true():
    adp = MockAdapter([
        tool_turn("c1", "write", {"path": "x"}),
        text_turn("done"),
    ])
    tools = {"write": DangerousWrite()}

    async def allow(_use, _ctx):
        return True

    hooks = Hooks(pre_tool_use=[make_approval_hook(tools, allow)])
    loop = AgentLoop(adapter=adp, tools=tools, hooks=hooks)
    events = asyncio.run(_drive(loop, "go"))
    results = _tool_results(events)
    assert results[0].is_error is False
    assert results[0].content == "wrote"


def test_approval_hook_remembers_approval_across_calls():
    adp = MockAdapter([
        tool_turn("c1", "write", {"path": "a"}),
        tool_turn("c2", "write", {"path": "b"}),
        text_turn("done"),
    ])
    tools = {"write": DangerousWrite()}
    calls = {"n": 0}

    async def approve_once(_use, _ctx):
        calls["n"] += 1
        return True

    hooks = Hooks(pre_tool_use=[make_approval_hook(tools, approve_once)])
    loop = AgentLoop(adapter=adp, tools=tools, hooks=hooks)
    asyncio.run(_drive(loop, "go"))
    assert calls["n"] == 1


def test_approval_hook_skips_safe_tools():
    adp = MockAdapter([
        tool_turn("c1", "read", {"path": "x"}),
        text_turn("done"),
    ])
    tools = {"read": SafeRead()}
    called = {"n": 0}

    async def approver(_u, _c):
        called["n"] += 1
        return False

    hooks = Hooks(pre_tool_use=[make_approval_hook(tools, approver)])
    loop = AgentLoop(adapter=adp, tools=tools, hooks=hooks)
    events = asyncio.run(_drive(loop, "go"))
    results = _tool_results(events)
    assert called["n"] == 0
    assert results[0].is_error is False


def test_hook_returning_none_emits_denial_error():
    adp = MockAdapter([
        tool_turn("c1", "read", {"path": "x"}),
        text_turn("noted"),
    ])
    tools = {"read": SafeRead()}

    async def deny_all(_u, _c):
        return None

    hooks = Hooks(pre_tool_use=[deny_all])
    loop = AgentLoop(adapter=adp, tools=tools, hooks=hooks)
    events = asyncio.run(_drive(loop, "go"))
    results = _tool_results(events)
    assert results[0].is_error is True
    assert "denied" in results[0].content.lower()


def test_detect_intent_patterns():
    assert detect_intent_without_action("I'll read the file next.")
    assert detect_intent_without_action("\u63a5\u4e0b\u6765\u6211\u6765\u6539")
    assert detect_intent_without_action("Let me search for the tests.")
    assert not detect_intent_without_action("Done. Result is 42.")
    assert not detect_intent_without_action("\u5b8c\u6210\u4e86")


def test_intent_hook_resumes_once_then_gives_up():
    adp = MockAdapter([
        text_turn("I'll look into it."),
        text_turn("Let me check the logs."),
        text_turn("I will search now."),
    ])
    hooks = Hooks(on_stop=[make_intent_without_action_hook(max_nudges=2)])
    loop = AgentLoop(adapter=adp, tools={}, hooks=hooks)
    events = asyncio.run(_drive(loop, "help"))

    msgs = [e for e in events if isinstance(e, Message)]
    assistant_msgs = [m for m in msgs if m.role == Role.ASSISTANT]
    user_nudges = [m for m in msgs if m.role == Role.USER]
    assert len(assistant_msgs) == 3
    assert len(user_nudges) == 2
    for nudge in user_nudges:
        text = "".join(b.text for b in nudge.content if isinstance(b, TextBlock))
        assert "did not call a tool" in text


def test_intent_hook_quiet_when_tool_call_present():
    adp = MockAdapter([
        tool_turn("c1", "read", {"path": "x"}, leading_text="I'll read it."),
        text_turn("Found it: 42."),
    ])
    hooks = Hooks(on_stop=[make_intent_without_action_hook(max_nudges=2)])
    loop = AgentLoop(adapter=adp, tools={"read": SafeRead()}, hooks=hooks)
    events = asyncio.run(_drive(loop, "read"))
    msgs = [e for e in events if isinstance(e, Message)]
    assert len(msgs) == 3
    user_msg = next(m for m in msgs if m.role == Role.USER)
    assert any(isinstance(b, ToolResultBlock) for b in user_msg.content)


def test_intent_hook_quiet_when_no_intent_phrase():
    adp = MockAdapter([text_turn("Done.")])
    hooks = Hooks(on_stop=[make_intent_without_action_hook(max_nudges=2)])
    loop = AgentLoop(adapter=adp, tools={}, hooks=hooks)
    events = asyncio.run(_drive(loop, "hi"))
    msgs = [e for e in events if isinstance(e, Message)]
    assert len(msgs) == 1
    assert msgs[0].role == Role.ASSISTANT


def test_final_guard_resumes_when_claimed_file_write_has_no_evidence(tmp_path):
    target = tmp_path / "claimed.html"
    adp = MockAdapter([
        text_turn(f"I created `{target}`."),
        text_turn("I will call the tool now."),
    ])
    hooks = Hooks(on_stop=[make_final_guard_hook(max_nudges=1)])
    loop = AgentLoop(adapter=adp, tools={}, hooks=hooks)

    events = asyncio.run(_drive(loop, f"Please save to {target}"))

    user_texts = _text_user_messages(events)
    assert any("Delivery contract failed" in text for text in user_texts)
    assert len(adp.call_log) == 2
    assert not target.exists()


def test_final_guard_allows_real_write_evidence(tmp_path):
    target = tmp_path / "created.html"
    adp = MockAdapter([
        tool_turn("c1", "Write", {"path": str(target), "content": "<h1>ok</h1>"}),
        text_turn(f"I created `{target}`."),
    ])
    hooks = Hooks(on_stop=[make_final_guard_hook(max_nudges=1)])
    loop = AgentLoop(
        adapter=adp,
        tools={"Write": PrimitiveWriteTool()},
        hooks=hooks,
    )

    events = asyncio.run(_drive(loop, f"Please save to {target}"))

    user_texts = _text_user_messages(events)
    assert not any("Delivery contract failed" in text for text in user_texts)
    assert target.read_text(encoding="utf-8") == "<h1>ok</h1>"


def test_final_guard_resumes_when_command_claim_has_no_bash_evidence():
    adp = MockAdapter([
        text_turn("I ran the tests and they passed."),
        text_turn("I will run the command now."),
    ])
    hooks = Hooks(on_stop=[make_final_guard_hook(max_nudges=1)])
    loop = AgentLoop(adapter=adp, tools={}, hooks=hooks)

    events = asyncio.run(_drive(loop, "run tests"))

    user_texts = _text_user_messages(events)
    assert any("command execution claim" in text for text in user_texts)
