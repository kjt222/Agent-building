"""Tests for PreToolUse approval hook and intent-without-action Stop hook."""

from __future__ import annotations

import asyncio

from agent.core.hooks import (
    detect_intent_without_action,
    make_approval_hook,
    make_intent_without_action_hook,
)
from agent.core.loop import (
    AgentLoop,
    Hooks,
    LoopConfig,
    LoopContext,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from tests.unit.mock_adapter import MockAdapter, text_turn, tool_turn


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Approval hook
# --------------------------------------------------------------------------- #


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
    assert calls["n"] == 1  # second write reused the cached approval


def test_approval_hook_skips_safe_tools():
    adp = MockAdapter([
        tool_turn("c1", "read", {"path": "x"}),
        text_turn("done"),
    ])
    tools = {"read": SafeRead()}
    called = {"n": 0}

    async def approver(_u, _c):
        called["n"] += 1
        return False  # would deny — but must never be called for SAFE

    hooks = Hooks(pre_tool_use=[make_approval_hook(tools, approver)])
    loop = AgentLoop(adapter=adp, tools=tools, hooks=hooks)
    events = asyncio.run(_drive(loop, "go"))
    results = _tool_results(events)
    assert called["n"] == 0
    assert results[0].is_error is False


# --------------------------------------------------------------------------- #
# Dispatcher deny-branch (hook returns None)
# --------------------------------------------------------------------------- #


def test_hook_returning_none_emits_denial_error():
    adp = MockAdapter([
        tool_turn("c1", "read", {"path": "x"}),
        text_turn("noted"),
    ])
    tools = {"read": SafeRead()}

    async def deny_all(_u, _c):
        return None  # generic denial

    hooks = Hooks(pre_tool_use=[deny_all])
    loop = AgentLoop(adapter=adp, tools=tools, hooks=hooks)
    events = asyncio.run(_drive(loop, "go"))
    results = _tool_results(events)
    assert results[0].is_error is True
    assert "denied" in results[0].content.lower()


# --------------------------------------------------------------------------- #
# Intent-without-action Stop hook
# --------------------------------------------------------------------------- #


def test_detect_intent_patterns():
    assert detect_intent_without_action("I'll read the file next.")
    assert detect_intent_without_action("接下来我来改一下那行。")
    assert detect_intent_without_action("Let me search for the tests.")
    assert not detect_intent_without_action("Done. Result is 42.")
    assert not detect_intent_without_action("完成了。")


def test_intent_hook_resumes_once_then_gives_up():
    # Turn 1: text with intent, no tool → hook should nudge and resume
    # Turn 2: text again, still no tool → nudge again (max_nudges=2)
    # Turn 3: still text, no tool → hook at cap, loop ends
    adp = MockAdapter([
        text_turn("I'll look into it."),
        text_turn("Let me check the logs."),
        text_turn("I will search now."),
    ])
    hooks = Hooks(on_stop=[make_intent_without_action_hook(max_nudges=2)])
    loop = AgentLoop(adapter=adp, tools={}, hooks=hooks)
    events = asyncio.run(_drive(loop, "help"))

    msgs = [e for e in events if isinstance(e, Message)]
    # 3 assistant turns + 2 nudges (user messages appended by hook) = 5
    assistant_msgs = [m for m in msgs if m.role == Role.ASSISTANT]
    user_nudges = [m for m in msgs if m.role == Role.USER]
    assert len(assistant_msgs) == 3
    assert len(user_nudges) == 2
    for n in user_nudges:
        text = "".join(b.text for b in n.content if isinstance(b, TextBlock))
        assert "did not call a tool" in text


def test_intent_hook_quiet_when_tool_call_present():
    adp = MockAdapter([
        tool_turn("c1", "read", {"path": "x"}, leading_text="I'll read it."),
        text_turn("Found it: 42."),
    ])
    hooks = Hooks(on_stop=[make_intent_without_action_hook(max_nudges=2)])
    loop = AgentLoop(adapter=adp, tools={"read": SafeRead()}, hooks=hooks)
    events = asyncio.run(_drive(loop, "read"))
    # 2 assistant + 1 user tool_result = 3 messages. No extra nudge.
    msgs = [e for e in events if isinstance(e, Message)]
    assert len(msgs) == 3
    # The sole USER msg must be the tool_result, not a nudge.
    user_msg = next(m for m in msgs if m.role == Role.USER)
    assert any(isinstance(b, ToolResultBlock) for b in user_msg.content)


def test_intent_hook_quiet_when_no_intent_phrase():
    # Plain "done" text — hook should not resume.
    adp = MockAdapter([text_turn("Done.")])
    hooks = Hooks(on_stop=[make_intent_without_action_hook(max_nudges=2)])
    loop = AgentLoop(adapter=adp, tools={}, hooks=hooks)
    events = asyncio.run(_drive(loop, "hi"))
    msgs = [e for e in events if isinstance(e, Message)]
    assert len(msgs) == 1
    assert msgs[0].role == Role.ASSISTANT
