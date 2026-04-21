"""Contract tests for Claude-Code-style primitive file tools."""

from __future__ import annotations

import asyncio

from agent.core.loop import LoopConfig, LoopContext
from agent.tools_v2.primitives import EditTool, ReadTool, WriteTool


def _ctx() -> LoopContext:
    return LoopContext(config=LoopConfig())


def test_write_existing_file_requires_read_first(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    ctx = _ctx()

    result = asyncio.run(
        WriteTool().run({"path": str(target), "content": "new"}, ctx)
    )

    assert result.is_error is True
    assert "Read it first" in result.content
    assert target.read_text(encoding="utf-8") == "old"


def test_edit_requires_read_first(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    ctx = _ctx()

    result = asyncio.run(
        EditTool().run(
            {"path": str(target), "old_string": "old", "new_string": "new"},
            ctx,
        )
    )

    assert result.is_error is True
    assert "Read it first" in result.content
    assert target.read_text(encoding="utf-8") == "old"


def test_read_then_edit_succeeds_with_exact_unique_string(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    ctx = _ctx()

    read_result = asyncio.run(ReadTool().run({"path": str(target)}, ctx))
    edit_result = asyncio.run(
        EditTool().run(
            {"path": str(target), "old_string": "beta", "new_string": "gamma"},
            ctx,
        )
    )

    assert read_result.is_error is False
    assert edit_result.is_error is False
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


def test_edit_rejects_ambiguous_old_string_without_replace_all(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    ctx = _ctx()
    asyncio.run(ReadTool().run({"path": str(target)}, ctx))

    result = asyncio.run(
        EditTool().run(
            {"path": str(target), "old_string": "same", "new_string": "new"},
            ctx,
        )
    )

    assert result.is_error is True
    assert "appears 2 times" in result.content
    assert target.read_text(encoding="utf-8") == "same\nsame\n"
