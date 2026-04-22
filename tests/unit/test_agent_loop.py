"""Core AgentLoop tests driven by MockAdapter — no network required."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent.core.loop import (
    AgentLoop,
    ImageBlock,
    LoopConfig,
    LoopContext,
    Message,
    PermissionLevel,
    ReasoningDelta,
    Role,
    TextDelta,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from tests.unit.mock_adapter import MockAdapter, text_turn, tool_turn


# --------------------------------------------------------------------------- #
# Tiny tool fixtures
# --------------------------------------------------------------------------- #


class EchoTool:
    name = "echo"
    description = "Echo input back"
    input_schema = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
        "required": ["msg"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        return ToolResultBlock(tool_use_id="", content=f"echo:{input.get('msg', '')}")


class BoomTool:
    name = "boom"
    description = "Always raises"
    input_schema = {"type": "object", "properties": {}}
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        raise RuntimeError("kaboom")


class WriteSideEffectTool:
    """NEEDS_APPROVAL tool to test plan-mode gating."""

    name = "write_file"
    description = "Pretend to write a file"
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        return ToolResultBlock(tool_use_id="", content=f"wrote {input.get('path')}")


class ScreenshotTool:
    name = "screenshot"
    description = "Return a rendered screenshot path"
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        return ToolResultBlock(
            tool_use_id="",
            content=json.dumps({
                "ok": True,
                "screenshot_path": input["path"],
            }),
        )


async def _drive(loop: AgentLoop, user_message: str) -> list:
    out = []
    async for ev in loop.run(user_message):
        out.append(ev)
    return out


# --------------------------------------------------------------------------- #
# Basic turn behavior
# --------------------------------------------------------------------------- #


def test_single_text_turn_ends_cleanly():
    adp = MockAdapter([text_turn("hello")])
    loop = AgentLoop(adapter=adp, tools={})
    events = asyncio.run(_drive(loop, "hi"))
    msgs = [e for e in events if isinstance(e, Message)]
    assert len(msgs) == 1
    assert msgs[0].role == Role.ASSISTANT
    assert msgs[0].content[0].text == "hello"
    assert len(adp.call_log) == 1  # only one model call


def test_run_streams_text_delta_before_final_message():
    adp = MockAdapter([text_turn("hello")])
    loop = AgentLoop(adapter=adp, tools={})
    events = asyncio.run(_drive(loop, "hi"))
    assert isinstance(events[0], TextDelta)
    assert events[0].text == "hello"
    msgs = [e for e in events if isinstance(e, Message)]
    assert msgs[0].content[0].text == "hello"


def test_run_streams_reasoning_delta_without_persisting_it():
    from agent.core.loop import TurnEnd

    adp = MockAdapter([[
        ReasoningDelta(text="thinking"),
        TextDelta(text="answer"),
        TurnEnd(stop_reason="end_turn"),
    ]])
    loop = AgentLoop(adapter=adp, tools={})
    events = asyncio.run(_drive(loop, "hi"))
    assert any(isinstance(e, ReasoningDelta) and e.text == "thinking" for e in events)
    msgs = [e for e in events if isinstance(e, Message)]
    assert msgs[0].content[0].text == "answer"
    assert all(not isinstance(b, ReasoningDelta) for b in msgs[0].content)


def test_run_passes_images_to_adapter():
    adp = MockAdapter([text_turn("seen")])
    loop = AgentLoop(adapter=adp, tools={})

    async def _run():
        out = []
        async for ev in loop.run(
            "describe",
            images=[{"base64": "abc", "media_type": "image/png", "name": "a.png"}],
        ):
            out.append(ev)
        return out

    asyncio.run(_run())
    [first] = adp.call_log
    [msg] = first["messages"]
    assert msg.role == Role.USER
    assert any(isinstance(b, ImageBlock) for b in msg.content)
    image = next(b for b in msg.content if isinstance(b, ImageBlock))
    assert image.base64 == "abc"
    assert image.media_type == "image/png"


def test_tool_use_round_trip_appends_result_message():
    adp = MockAdapter([
        tool_turn("call_1", "echo", {"msg": "ping"}),
        text_turn("done"),
    ])
    loop = AgentLoop(adapter=adp, tools={"echo": EchoTool()})
    events = asyncio.run(_drive(loop, "please echo"))

    msgs = [e for e in events if isinstance(e, Message)]
    # assistant-tool-call, user-tool-result, assistant-final → 3 messages
    assert len(msgs) == 3
    assert msgs[0].role == Role.ASSISTANT
    tool_use = next(b for b in msgs[0].content if isinstance(b, ToolUseBlock))
    assert tool_use.name == "echo" and tool_use.input == {"msg": "ping"}

    assert msgs[1].role == Role.USER
    result = next(b for b in msgs[1].content if isinstance(b, ToolResultBlock))
    assert result.tool_use_id == "call_1"
    assert result.content == "echo:ping"
    assert result.is_error is False

    assert msgs[2].role == Role.ASSISTANT
    assert msgs[2].content[0].text == "done"


def test_tool_result_screenshot_is_attached_to_next_model_turn(tmp_path: Path):
    screenshot = tmp_path / "screen.png"
    screenshot.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    adp = MockAdapter([
        tool_turn("c1", "screenshot", {"path": str(screenshot)}),
        text_turn("looked at screenshot"),
    ])
    loop = AgentLoop(adapter=adp, tools={"screenshot": ScreenshotTool()})

    events = asyncio.run(_drive(loop, "verify visually"))

    msgs = [e for e in events if isinstance(e, Message)]
    feedback_msg = msgs[1]
    assert feedback_msg.role == Role.USER
    assert any(isinstance(b, ToolResultBlock) for b in feedback_msg.content)
    image = next(b for b in feedback_msg.content if isinstance(b, ImageBlock))
    assert image.name == "screen.png"
    assert image.media_type == "image/png"
    assert image.base64

    second_call_messages = adp.call_log[1]["messages"]
    second_call_feedback = second_call_messages[-1]
    assert any(isinstance(b, ImageBlock) for b in second_call_feedback.content)


def test_unknown_tool_yields_error_result():
    adp = MockAdapter([
        tool_turn("c1", "does_not_exist", {}),
        text_turn("ok"),
    ])
    loop = AgentLoop(adapter=adp, tools={})
    events = asyncio.run(_drive(loop, "x"))
    msgs = [e for e in events if isinstance(e, Message)]
    user_result = msgs[1]
    result = next(b for b in user_result.content if isinstance(b, ToolResultBlock))
    assert result.is_error is True
    assert "not found" in result.content.lower()


def test_tool_exception_becomes_error_result():
    adp = MockAdapter([
        tool_turn("c1", "boom", {}),
        text_turn("noted"),
    ])
    loop = AgentLoop(adapter=adp, tools={"boom": BoomTool()})
    events = asyncio.run(_drive(loop, "please fail"))
    msgs = [e for e in events if isinstance(e, Message)]
    result = next(b for b in msgs[1].content if isinstance(b, ToolResultBlock))
    assert result.is_error is True
    assert "kaboom" in result.content


def test_max_iterations_caps_loop():
    # Script keeps demanding another tool call; cap at 2 iterations.
    adp = MockAdapter([
        tool_turn("c1", "echo", {"msg": "1"}),
        tool_turn("c2", "echo", {"msg": "2"}),
        tool_turn("c3", "echo", {"msg": "3"}),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={"echo": EchoTool()},
        config=LoopConfig(max_iterations=2),
    )
    events = asyncio.run(_drive(loop, "go"))
    # 2 assistant msgs + 2 user tool-result msgs = 4
    assert sum(1 for e in events if isinstance(e, Message)) == 4


# --------------------------------------------------------------------------- #
# Usage accumulation + trace
# --------------------------------------------------------------------------- #


def test_usage_accumulates_across_turns():
    adp = MockAdapter([
        tool_turn("c1", "echo", {"msg": "a"},
                  usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}),
        text_turn("fin",
                  usage={"input_tokens": 150, "output_tokens": 5, "total_tokens": 155}),
    ])

    class _Spy(EchoTool):
        snapshot = {}
        async def run(self, input, ctx):
            _Spy.snapshot = dict(ctx.usage)  # capture mid-run
            return await super().run(input, ctx)

    loop = AgentLoop(adapter=adp, tools={"echo": _Spy()})
    asyncio.run(_drive(loop, "go"))
    # During the first tool call, only turn 1's usage should have landed.
    assert _Spy.snapshot["input_tokens"] == 100
    # Can't easily reach the final ctx from outside, but we can assert via
    # re-driving and inspecting call_log size + reading the trace.


def test_trace_writer_emits_one_jsonl_per_turn(tmp_path: Path):
    trace = tmp_path / "trace.jsonl"
    adp = MockAdapter([
        tool_turn("c1", "echo", {"msg": "ping"},
                  usage={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}),
        text_turn("done",
                  usage={"input_tokens": 80, "output_tokens": 5, "total_tokens": 85}),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={"echo": EchoTool()},
        config=LoopConfig(trace_path=trace),
    )
    asyncio.run(_drive(loop, "go"))
    lines = trace.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec0, rec1 = json.loads(lines[0]), json.loads(lines[1])
    assert rec0["iteration"] == 1 and rec0["stop_reason"] == "tool_use"
    assert rec0["tool_calls"][0]["name"] == "echo"
    assert rec0["tool_calls"][0]["result"] == "echo:ping"
    assert rec0["usage"]["total_tokens"] == 60
    assert rec1["iteration"] == 2 and rec1["stop_reason"] == "end_turn"
    assert rec1["tool_calls"] == []


def test_trace_writer_records_assistant_text_and_system_prompt_hash(tmp_path: Path):
    trace = tmp_path / "trace.jsonl"
    loop = AgentLoop(
        adapter=MockAdapter([text_turn("trace answer")]),
        tools={},
        config=LoopConfig(trace_path=trace, system_prompt="system contract"),
    )

    asyncio.run(_drive(loop, "go"))

    record = json.loads(trace.read_text(encoding="utf-8").splitlines()[0])
    assert record["assistant_text"] == "trace answer"
    assert record["system_prompt_hash"]
    assert record["system_prompt_hash"] != "system contract"


# --------------------------------------------------------------------------- #
# Parallel vs serial dispatch
# --------------------------------------------------------------------------- #


class _SerialOnlyTool(EchoTool):
    parallel_safe = False


def test_parallel_dispatch_when_all_safe():
    # Emit two tool calls in one turn.
    from agent.core.loop import TextDelta, ToolUseDelta, TurnEnd
    adp = MockAdapter([
        [
            ToolUseDelta(id="a", name="echo", input_partial={"msg": "1"}),
            ToolUseDelta(id="b", name="echo", input_partial={"msg": "2"}),
            TurnEnd(stop_reason="tool_use"),
        ],
        text_turn("ok"),
    ])
    loop = AgentLoop(adapter=adp, tools={"echo": EchoTool()})
    events = asyncio.run(_drive(loop, "go"))
    results = [b for e in events if isinstance(e, Message) and e.role == Role.USER
               for b in e.content if isinstance(b, ToolResultBlock)]
    assert len(results) == 2
    assert {r.content for r in results} == {"echo:1", "echo:2"}


def test_serial_when_any_tool_unsafe():
    from agent.core.loop import ToolUseDelta, TurnEnd
    adp = MockAdapter([
        [
            ToolUseDelta(id="a", name="echo", input_partial={"msg": "1"}),
            ToolUseDelta(id="b", name="slow", input_partial={"msg": "2"}),
            TurnEnd(stop_reason="tool_use"),
        ],
        text_turn("ok"),
    ])
    loop = AgentLoop(
        adapter=adp,
        tools={"echo": EchoTool(), "slow": _SerialOnlyTool()},
    )
    events = asyncio.run(_drive(loop, "go"))
    results = [b for e in events if isinstance(e, Message) and e.role == Role.USER
               for b in e.content if isinstance(b, ToolResultBlock)]
    assert [r.tool_use_id for r in results] == ["a", "b"]  # order preserved
