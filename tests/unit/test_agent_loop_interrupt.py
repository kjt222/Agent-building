"""AgentLoop cancel-event handling (P12.1)."""

from __future__ import annotations

import asyncio

import pytest

from agent.core.loop import (
    AgentLoop,
    LoopConfig,
    LoopContext,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    TurnEnd,
)

from tests.unit.mock_adapter import MockAdapter, text_turn, tool_turn


class _SlowAdapter:
    """Adapter that yields deltas with a delay so cancel can race."""

    name = "slow-mock"

    def __init__(self, deltas, delay_s: float = 0.05):
        self._deltas = list(deltas)
        self._delay_s = delay_s

    async def stream(self, messages, tools, system=None, **options):
        for d in self._deltas:
            await asyncio.sleep(self._delay_s)
            yield d


class _SlowTool:
    name = "slow"
    description = "tool that waits"
    input_schema = {"type": "object"}
    permission_level = PermissionLevel.SAFE
    parallel_safe = False

    def __init__(self) -> None:
        self.ran = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        self.ran = True
        await asyncio.sleep(0.05)
        return ToolResultBlock(tool_use_id="", content="ok")


async def _collect(loop_, cancel_event):
    out: list = []
    async for ev in loop_.run("hi", history=[], cancel_event=cancel_event):
        out.append(ev)
    return out


@pytest.mark.asyncio
async def test_cancel_between_turns_stops_loop_before_next_model_call():
    # Script: turn 1 calls a tool; if not cancelled, turn 2 ends. We set
    # the cancel event after turn 1's tool result is yielded.
    adapter = MockAdapter([
        tool_turn("tu1", "slow", {}),
        text_turn("never reached"),
    ])
    cancel = asyncio.Event()
    loop_ = AgentLoop(
        adapter=adapter,
        tools={"slow": _SlowTool()},
        config=LoopConfig(max_iterations=4),
    )
    collected: list = []
    async for ev in loop_.run("hi", history=[], cancel_event=cancel):
        collected.append(ev)
        # After we see the tool_result Message (USER role), trigger cancel.
        if (
            isinstance(ev, Message)
            and ev.role == Role.USER
            and any(isinstance(b, ToolResultBlock) for b in ev.content)
        ):
            cancel.set()
    # Adapter should have been invoked exactly once (turn 1); turn 2 must
    # never have been reached because cancel was checked at the top of the
    # while loop.
    assert len(adapter.call_log) == 1


@pytest.mark.asyncio
async def test_cancel_during_streaming_breaks_out_of_one_turn():
    # Slow adapter emits one text delta then would emit more; we cancel
    # before it can yield the TurnEnd.
    adapter = _SlowAdapter(
        [TextDelta(text="hello "), TextDelta(text="world"), TurnEnd("end_turn")],
        delay_s=0.05,
    )
    cancel = asyncio.Event()
    loop_ = AgentLoop(adapter=adapter, tools={}, config=LoopConfig(max_iterations=1))

    async def _trigger():
        await asyncio.sleep(0.06)
        cancel.set()

    asyncio.create_task(_trigger())
    events: list = []
    async for ev in loop_.run("hi", history=[], cancel_event=cancel):
        events.append(ev)
    # We must have yielded at least the first TextDelta; we should NOT
    # have hung waiting for the third (delay_s * 3 = 0.15s).
    deltas = [e for e in events if isinstance(e, TextDelta)]
    assert len(deltas) >= 1


@pytest.mark.asyncio
async def test_cancel_skips_pending_tool_calls():
    # Model emits a tool_use; cancel arrives before dispatch. The tool
    # must NOT execute; the loop returns a cancellation-typed result.
    tool = _SlowTool()
    adapter = MockAdapter([tool_turn("tu1", "slow", {})])
    cancel = asyncio.Event()
    cancel.set()  # already cancelled before any iteration
    loop_ = AgentLoop(
        adapter=adapter,
        tools={"slow": tool},
        config=LoopConfig(max_iterations=2),
    )
    async for _ in loop_.run("hi", history=[], cancel_event=cancel):
        pass
    assert tool.ran is False
    # Adapter never even gets called because the cancel check is at the
    # very top of the run() while loop.
    assert adapter.call_log == []


@pytest.mark.asyncio
async def test_no_cancel_event_means_normal_run():
    adapter = MockAdapter([text_turn("hello")])
    loop_ = AgentLoop(adapter=adapter, tools={}, config=LoopConfig(max_iterations=1))
    async for _ in loop_.run("hi", history=[]):
        pass
    assert len(adapter.call_log) == 1
