"""Tests for AskUserQuestion (P12.3)."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.core.loop import LoopConfig, LoopContext
from agent.tools_v2.control import AskUserQuestionTool


@pytest.mark.asyncio
async def test_rejects_empty_question():
    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    result = await tool.run({"question": "   "}, ctx)
    assert result.is_error is True
    assert "non-empty" in result.content


@pytest.mark.asyncio
async def test_rejects_options_not_a_list():
    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    result = await tool.run(
        {"question": "Which one?", "options": "a,b,c"}, ctx
    )
    assert result.is_error is True


@pytest.mark.asyncio
async def test_rejects_too_many_options():
    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    result = await tool.run(
        {"question": "Which?", "options": ["a", "b", "c", "d", "e"]}, ctx
    )
    assert result.is_error is True


@pytest.mark.asyncio
async def test_no_handler_returns_clean_error():
    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    result = await tool.run({"question": "Where should I save?"}, ctx)
    # Without a handler the tool must not hang. The model should see an
    # error pointing at "pick a default".
    assert result.is_error is True
    assert "default" in result.content.lower()


@pytest.mark.asyncio
async def test_handler_is_called_and_reply_is_returned():
    received_payload: dict = {}

    async def handler(payload: dict) -> dict:
        received_payload.update(payload)
        return {"answer": "PDF format", "selected_option": "PDF"}

    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    ctx.scratch["user_question_handler"] = handler

    result = await tool.run(
        {
            "question": "Which output format?",
            "options": ["PDF", "DOCX"],
            "context": "We will email this to reviewers.",
        },
        ctx,
    )

    assert result.is_error is False
    body = json.loads(result.content)
    assert body["type"] == "user_question_reply"
    assert body["answer"] == "PDF format"
    assert body["selected_option"] == "PDF"

    # The handler payload should carry everything the UI needs to render.
    assert received_payload["question"] == "Which output format?"
    assert received_payload["options"] == ["PDF", "DOCX"]
    assert received_payload["multi_select"] is False
    assert received_payload["context"] == "We will email this to reviewers."
    assert received_payload["question_id"]  # uuid hex set

    # The exchange is recorded for the trace.
    history = ctx.scratch["user_questions"]
    assert len(history) == 1
    assert history[0]["reply"]["selected_option"] == "PDF"


@pytest.mark.asyncio
async def test_handler_timeout_returns_clean_error():
    async def handler(_payload):
        raise asyncio.TimeoutError()

    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    ctx.scratch["user_question_handler"] = handler
    result = await tool.run({"question": "Where?"}, ctx)
    assert result.is_error is True
    assert "in time" in result.content


@pytest.mark.asyncio
async def test_handler_exception_returns_clean_error():
    async def handler(_payload):
        raise RuntimeError("UI gone")

    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    ctx.scratch["user_question_handler"] = handler
    result = await tool.run({"question": "X?"}, ctx)
    assert result.is_error is True
    assert "RuntimeError" in result.content


@pytest.mark.asyncio
async def test_multi_select_passed_to_handler():
    captured = {}

    async def handler(payload):
        captured.update(payload)
        return {
            "answer": "PDF, DOCX",
            "selected_options": ["PDF", "DOCX"],
        }

    tool = AskUserQuestionTool()
    ctx = LoopContext(config=LoopConfig())
    ctx.scratch["user_question_handler"] = handler
    result = await tool.run(
        {
            "question": "Which formats?",
            "options": ["PDF", "DOCX", "HTML"],
            "multi_select": True,
        },
        ctx,
    )
    assert result.is_error is False
    assert captured["multi_select"] is True
    body = json.loads(result.content)
    assert body["selected_options"] == ["PDF", "DOCX"]
