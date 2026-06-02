"""Tests for the agentic exit_plan_mode flow (P12.4.x).

ExitPlanModeTool now consults ``ctx.scratch["plan_approval_handler"]`` when
present and only flips ``plan_exited`` after the handler returns approved=True.
"""

from __future__ import annotations

import pytest

from agent.core.loop import LoopConfig, LoopContext, ToolResultBlock
from agent.tools_v2.control import ExitPlanModeTool


def _ctx() -> LoopContext:
    return LoopContext(config=LoopConfig())


@pytest.mark.asyncio
async def test_empty_plan_rejected_before_handler():
    tool = ExitPlanModeTool()
    called: dict = {"n": 0}

    async def _handler(_p):
        called["n"] += 1
        return {"approved": True}

    ctx = _ctx()
    ctx.scratch["plan_approval_handler"] = _handler
    result = await tool.run({"plan": "   "}, ctx)
    assert isinstance(result, ToolResultBlock)
    assert result.is_error is True
    assert called["n"] == 0
    assert "plan_exited" not in ctx.scratch


@pytest.mark.asyncio
async def test_handler_approved_flips_gate():
    tool = ExitPlanModeTool()
    captured: dict = {}

    async def _handler(payload):
        captured.update(payload)
        return {"approved": True, "revision_note": "looks good"}

    ctx = _ctx()
    ctx.scratch["plan_approval_handler"] = _handler
    ctx.scratch["conversation_id"] = "conv_x"
    result = await tool.run({"plan": "Step 1: do X. Step 2: do Y."}, ctx)
    assert result.is_error is False
    assert ctx.scratch.get("plan_exited") is True
    assert ctx.scratch.get("plan_approved") is True
    assert "looks good" in result.content
    assert captured["plan"].startswith("Step 1")
    assert captured["conversation_id"] == "conv_x"
    assert "plan_id" in captured
    hist = ctx.scratch.get("plan_history") or []
    assert hist and hist[-1]["approved"] is True


@pytest.mark.asyncio
async def test_handler_rejected_keeps_gate_closed():
    tool = ExitPlanModeTool()

    async def _handler(_p):
        return {"approved": False, "revision_note": "too risky"}

    ctx = _ctx()
    ctx.scratch["plan_approval_handler"] = _handler
    result = await tool.run({"plan": "do something risky"}, ctx)
    assert result.is_error is True
    assert ctx.scratch.get("plan_exited") is not True
    assert "too risky" in result.content
    hist = ctx.scratch.get("plan_history") or []
    assert hist and hist[-1]["approved"] is False


@pytest.mark.asyncio
async def test_handler_exception_keeps_gate_closed():
    tool = ExitPlanModeTool()

    async def _handler(_p):
        raise RuntimeError("UI exploded")

    ctx = _ctx()
    ctx.scratch["plan_approval_handler"] = _handler
    result = await tool.run({"plan": "do X"}, ctx)
    assert result.is_error is True
    assert "RuntimeError" in result.content
    assert ctx.scratch.get("plan_exited") is not True


@pytest.mark.asyncio
async def test_no_handler_falls_back_to_legacy_auto_flip():
    """CLI / unit / batch runs without a UI must still work."""
    tool = ExitPlanModeTool()
    ctx = _ctx()
    result = await tool.run({"plan": "Step 1: do X."}, ctx)
    assert result.is_error is False
    assert ctx.scratch.get("plan_exited") is True
    assert ctx.scratch.get("plan_text") == "Step 1: do X."


@pytest.mark.asyncio
async def test_handler_rejected_with_non_dict_reply_keeps_gate_closed():
    tool = ExitPlanModeTool()

    async def _handler(_p):
        return None  # bad handler contract

    ctx = _ctx()
    ctx.scratch["plan_approval_handler"] = _handler
    result = await tool.run({"plan": "X"}, ctx)
    assert result.is_error is True
    assert ctx.scratch.get("plan_exited") is not True
