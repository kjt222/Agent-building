"""P2 behavior replay tests for Claude-Code-style tool contracts."""

from __future__ import annotations

import asyncio

from agent.core.hooks import make_final_guard_hook
from agent.core.loop import AgentLoop, Hooks, Message, Role, TextBlock, ToolResultBlock
from agent.tools_v2.primitives import BashTool, EditTool, ReadTool, WriteTool

from tests.unit.mock_adapter import MockAdapter, text_turn, tool_turn


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


def _user_texts(events) -> list[str]:
    return [
        "".join(b.text for b in e.content if isinstance(b, TextBlock))
        for e in events
        if isinstance(e, Message) and e.role == Role.USER
    ]


def test_replay_edit_failure_then_read_and_exact_edit(tmp_path):
    target = tmp_path / "bug.py"
    target.write_text("bug = True\n", encoding="utf-8")
    adp = MockAdapter([
        tool_turn(
            "e1",
            "Edit",
            {"path": str(target), "old_string": "bug = True", "new_string": "bug = False"},
        ),
        tool_turn("r1", "Read", {"path": str(target)}),
        tool_turn(
            "e2",
            "Edit",
            {"path": str(target), "old_string": "bug = True", "new_string": "bug = False"},
        ),
        text_turn(f"Fixed `{target}`."),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={"Read": ReadTool(), "Edit": EditTool()},
        hooks=Hooks(on_stop=[make_final_guard_hook(max_nudges=1)]),
    )

    events = asyncio.run(_drive(loop, "Fix the bug in the existing file."))

    results = _tool_results(events)
    assert results[0].is_error is True
    assert "Read it first" in results[0].content
    assert results[1].is_error is False
    assert results[2].is_error is False
    assert target.read_text(encoding="utf-8") == "bug = False\n"
    assert not any("Delivery contract failed" in text for text in _user_texts(events))


def test_replay_final_guard_forces_write_after_false_delivery(tmp_path):
    target = tmp_path / "artifact.html"
    adp = MockAdapter([
        text_turn(f"I created `{target}`."),
        tool_turn("w1", "Write", {"path": str(target), "content": "<h1>ok</h1>"}),
        text_turn(f"I created `{target}`."),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={"Write": WriteTool()},
        hooks=Hooks(on_stop=[make_final_guard_hook(max_nudges=1)]),
    )

    events = asyncio.run(_drive(loop, f"Please save to {target}"))

    assert any("Delivery contract failed" in text for text in _user_texts(events))
    assert target.read_text(encoding="utf-8") == "<h1>ok</h1>"


def test_replay_bash_blocks_dangerous_git_mutation():
    adp = MockAdapter([
        tool_turn("b1", "Bash", {"command": "git push"}),
        text_turn("Blocked: git push is not allowed in this runtime."),
    ])
    loop = AgentLoop(adapter=adp, tools={"Bash": BashTool()})

    events = asyncio.run(_drive(loop, "Push the branch."))

    [result] = _tool_results(events)
    assert result.is_error is True
    assert "git subcommand" in result.content
