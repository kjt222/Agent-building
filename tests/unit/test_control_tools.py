"""Plan mode and subagent tests."""

from __future__ import annotations

import asyncio

from agent.core.loop import (
    AgentLoop,
    LoopConfig,
    LoopContext,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agent.tools_v2.control import AgentTool, ExitPlanModeTool, SubagentPreset

from tests.unit.mock_adapter import MockAdapter, text_turn, tool_turn


# --------------------------------------------------------------------------- #
# Shared fixtures
# --------------------------------------------------------------------------- #


class ReadTool:
    name = "read"
    description = "Read something"
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        return ToolResultBlock(tool_use_id="", content=f"read:{input.get('path')}")


class WriteTool:
    name = "write"
    description = "Write something"
    input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        return ToolResultBlock(tool_use_id="", content=f"wrote:{input.get('path')}")


async def _drive(loop: AgentLoop, user_message: str) -> list:
    out = []
    async for ev in loop.run(user_message):
        out.append(ev)
    return out


# --------------------------------------------------------------------------- #
# Plan mode
# --------------------------------------------------------------------------- #


def test_plan_mode_blocks_write_until_exit():
    adp = MockAdapter([
        tool_turn("c1", "write", {"path": "a.txt"}),  # blocked
        tool_turn("c2", "exit_plan_mode", {"plan": "I will fix X by editing Y."}),
        tool_turn("c3", "write", {"path": "a.txt"}),  # allowed now
        text_turn("done"),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={
            "read": ReadTool(),
            "write": WriteTool(),
            "exit_plan_mode": ExitPlanModeTool(),
        },
        config=LoopConfig(permission_mode="plan"),
    )
    events = asyncio.run(_drive(loop, "go"))
    results_by_turn = [
        [b for b in e.content if isinstance(b, ToolResultBlock)]
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
    ]
    # turn 1: write blocked
    assert results_by_turn[0][0].is_error is True
    assert "plan mode" in results_by_turn[0][0].content.lower()
    # turn 2: exit_plan_mode succeeded
    assert results_by_turn[1][0].is_error is False
    # turn 3: write succeeded
    assert results_by_turn[2][0].is_error is False
    assert results_by_turn[2][0].content == "wrote:a.txt"


def test_plan_mode_allows_safe_tools():
    adp = MockAdapter([
        tool_turn("c1", "read", {"path": "a.txt"}),
        text_turn("seen"),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={"read": ReadTool()},
        config=LoopConfig(permission_mode="plan"),
    )
    events = asyncio.run(_drive(loop, "investigate"))
    result = next(
        b
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
        for b in e.content
        if isinstance(b, ToolResultBlock)
    )
    assert result.is_error is False
    assert result.content == "read:a.txt"


def test_exit_plan_mode_requires_nonempty_plan():
    adp = MockAdapter([
        tool_turn("c1", "exit_plan_mode", {"plan": ""}),
        text_turn("oops"),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={"exit_plan_mode": ExitPlanModeTool()},
        config=LoopConfig(permission_mode="plan"),
    )
    events = asyncio.run(_drive(loop, "x"))
    result = next(
        b
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
        for b in e.content
        if isinstance(b, ToolResultBlock)
    )
    assert result.is_error is True


# --------------------------------------------------------------------------- #
# Subagent (AgentTool)
# --------------------------------------------------------------------------- #


def test_agent_tool_runs_subagent_and_returns_final_text():
    # Subagent does one read then finishes with a summary.
    sub_adapter = MockAdapter([
        tool_turn("s1", "read", {"path": "x"}),
        text_turn("file contains 42"),
    ])
    preset = SubagentPreset(
        adapter=sub_adapter,
        tools={"read": ReadTool()},
        system_prompt="Be brief.",
        max_iterations=10,
        description="investigator",
    )
    agent_tool = AgentTool(presets={"default": preset})

    # Parent calls AgentTool once, then stops.
    parent_adapter = MockAdapter([
        tool_turn("p1", "Agent", {
            "description": "inspect file",
            "prompt": "Tell me what's in x.",
        }),
        text_turn("acknowledged"),
    ])
    parent = AgentLoop(
        adapter=parent_adapter,
        tools={"Agent": agent_tool},
    )
    events = asyncio.run(_drive(parent, "delegate"))

    # Grab the tool result from the parent's transcript.
    result = next(
        b
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
        for b in e.content
        if isinstance(b, ToolResultBlock)
    )
    assert result.is_error is False
    assert result.content == "file contains 42"


def test_agent_tool_unknown_preset_errors():
    sub_adapter = MockAdapter([])
    preset = SubagentPreset(
        adapter=sub_adapter, tools={}, system_prompt="p", max_iterations=1
    )
    agent_tool = AgentTool(presets={"default": preset})
    parent_adapter = MockAdapter([
        tool_turn("p1", "Agent", {
            "description": "x",
            "prompt": "hi",
            "subagent_type": "does_not_exist",
        }),
        text_turn("ok"),
    ])
    parent = AgentLoop(adapter=parent_adapter, tools={"Agent": agent_tool})
    events = asyncio.run(_drive(parent, "x"))
    result = next(
        b
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
        for b in e.content
        if isinstance(b, ToolResultBlock)
    )
    assert result.is_error is True
    assert "does_not_exist" in result.content


def test_agent_tool_rejects_empty_prompt():
    sub_adapter = MockAdapter([])
    preset = SubagentPreset(
        adapter=sub_adapter, tools={}, system_prompt="p", max_iterations=1
    )
    agent_tool = AgentTool(presets={"default": preset})
    parent_adapter = MockAdapter([
        tool_turn("p1", "Agent", {"description": "x", "prompt": ""}),
        text_turn("ok"),
    ])
    parent = AgentLoop(adapter=parent_adapter, tools={"Agent": agent_tool})
    events = asyncio.run(_drive(parent, "x"))
    result = next(
        b
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
        for b in e.content
        if isinstance(b, ToolResultBlock)
    )
    assert result.is_error is True
