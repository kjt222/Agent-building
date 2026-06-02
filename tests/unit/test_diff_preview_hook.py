"""Tests for the P12.2 diff-preview PreToolUse hook."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.core.hooks import (
    build_edit_diff,
    build_write_diff,
    make_diff_preview_hook,
)
from agent.core.loop import (
    LoopConfig,
    LoopContext,
    PermissionLevel,
    ToolResultBlock,
    ToolUseBlock,
)


# --------------------------------------------------------------------------- #
# Diff builders
# --------------------------------------------------------------------------- #


def test_build_write_diff_for_new_file(tmp_path):
    path = tmp_path / "new.txt"
    use = ToolUseBlock(
        id="t1",
        name="Write",
        input={"path": str(path), "content": "hello\nworld\n"},
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    payload = build_write_diff(use, ctx)
    assert payload is not None
    assert payload["tool"] == "Write"
    assert payload["exists"] is False
    assert payload["after_lines"] == 2
    assert "+hello" in payload["unified_diff"]
    assert "+world" in payload["unified_diff"]


def test_build_write_diff_for_overwrite(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    use = ToolUseBlock(
        id="t1",
        name="Write",
        input={"path": str(path), "content": "alpha\nGAMMA\n"},
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    payload = build_write_diff(use, ctx)
    assert payload is not None
    assert payload["exists"] is True
    assert "-beta" in payload["unified_diff"]
    assert "+GAMMA" in payload["unified_diff"]


def test_build_edit_diff_single_occurrence(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("foo bar\nbar baz\n", encoding="utf-8")
    use = ToolUseBlock(
        id="t1",
        name="Edit",
        input={"path": str(path), "old_string": "foo", "new_string": "FOO"},
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    payload = build_edit_diff(use, ctx)
    assert payload is not None
    assert payload["occurrences_changed"] == 1
    assert payload["replace_all"] is False
    assert "-foo bar" in payload["unified_diff"]
    assert "+FOO bar" in payload["unified_diff"]


def test_build_edit_diff_replace_all(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("x x x\nx\n", encoding="utf-8")
    use = ToolUseBlock(
        id="t1",
        name="Edit",
        input={
            "path": str(path),
            "old_string": "x",
            "new_string": "Y",
            "replace_all": True,
        },
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    payload = build_edit_diff(use, ctx)
    assert payload is not None
    assert payload["occurrences_changed"] == 4
    assert payload["replace_all"] is True


def test_build_edit_diff_returns_none_when_old_string_missing(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("alpha\n", encoding="utf-8")
    use = ToolUseBlock(
        id="t1",
        name="Edit",
        input={"path": str(path), "old_string": "missing", "new_string": "Y"},
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    assert build_edit_diff(use, ctx) is None


# --------------------------------------------------------------------------- #
# Hook behaviour
# --------------------------------------------------------------------------- #


class _DummyTool:
    name = "Write"
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False
    description = ""
    input_schema = {}


@pytest.mark.asyncio
async def test_hook_passes_through_when_no_handler(tmp_path):
    use = ToolUseBlock(
        id="t1",
        name="Write",
        input={"path": str(tmp_path / "f.txt"), "content": "x"},
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    hook = make_diff_preview_hook({"Write": _DummyTool()}, None)
    out = await hook(use, ctx)
    assert out is use


@pytest.mark.asyncio
async def test_hook_passes_through_for_non_target_tools(tmp_path):
    use = ToolUseBlock(id="t1", name="Bash", input={"command": "ls"})
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    called: dict = {"n": 0}

    async def _handler(_p):
        called["n"] += 1
        return {"approved": False}

    hook = make_diff_preview_hook({"Bash": _DummyTool()}, _handler)
    out = await hook(use, ctx)
    assert out is use
    assert called["n"] == 0  # never reached


@pytest.mark.asyncio
async def test_hook_approves_lets_through_and_caches(tmp_path):
    path = tmp_path / "f.txt"
    use = ToolUseBlock(
        id="t1", name="Write", input={"path": str(path), "content": "hi"}
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    captured: dict = {}

    async def _handler(payload):
        captured.update(payload)
        return {"approved": True}

    hook = make_diff_preview_hook({"Write": _DummyTool()}, _handler)
    out = await hook(use, ctx)
    assert out is use
    assert captured["tool"] == "Write"
    assert "+hi" in captured["unified_diff"]
    # Standard approval cache pre-populated.
    assert "Write" in ctx.scratch.get("approved_tools", set())


@pytest.mark.asyncio
async def test_hook_rejects_returns_tool_result(tmp_path):
    path = tmp_path / "f.txt"
    use = ToolUseBlock(
        id="t1", name="Write", input={"path": str(path), "content": "hi"}
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))

    async def _handler(_p):
        return {"approved": False, "note": "no"}

    hook = make_diff_preview_hook({"Write": _DummyTool()}, _handler)
    out = await hook(use, ctx)
    assert isinstance(out, ToolResultBlock)
    assert out.is_error is True
    assert "rejected" in out.content.lower()


@pytest.mark.asyncio
async def test_hook_handler_exception_short_circuits(tmp_path):
    path = tmp_path / "f.txt"
    use = ToolUseBlock(
        id="t1", name="Write", input={"path": str(path), "content": "hi"}
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))

    async def _handler(_p):
        raise RuntimeError("ui crashed")

    hook = make_diff_preview_hook({"Write": _DummyTool()}, _handler)
    out = await hook(use, ctx)
    assert isinstance(out, ToolResultBlock)
    assert out.is_error is True
    assert "RuntimeError" in out.content


@pytest.mark.asyncio
async def test_hook_passes_through_when_diff_is_empty(tmp_path):
    """If the proposed Write content is identical to existing file, diff is empty."""
    path = tmp_path / "f.txt"
    path.write_text("same\n", encoding="utf-8")
    use = ToolUseBlock(
        id="t1", name="Write", input={"path": str(path), "content": "same\n"}
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    handler_called = {"n": 0}

    async def _handler(_p):
        handler_called["n"] += 1
        return {"approved": True}

    hook = make_diff_preview_hook({"Write": _DummyTool()}, _handler)
    out = await hook(use, ctx)
    assert out is use
    assert handler_called["n"] == 0  # no diff, no preview, no double-gating


# --------------------------------------------------------------------------- #
# Excel / PowerPoint routing (P12.2.2)
# --------------------------------------------------------------------------- #


class _ExcelRuntimeDummy:
    name = "ExcelRuntimeEdit"
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False
    description = ""
    input_schema = {}


class _PowerPointRuntimeDummy:
    name = "PowerPointRuntimeEdit"
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False
    description = ""
    input_schema = {}


@pytest.mark.asyncio
async def test_hook_routes_excel_runtime_via_builder(tmp_path):
    use = ToolUseBlock(
        id="e1",
        name="ExcelRuntimeEdit",
        input={
            "path": "book.xlsx",
            "ops": [{"op": "set_cell", "sheet": "S", "cell": "A1", "value": 42}],
        },
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    captured: dict = {}

    async def _handler(payload):
        captured.update(payload)
        return {"approved": True}

    hook = make_diff_preview_hook(
        {"ExcelRuntimeEdit": _ExcelRuntimeDummy()}, _handler
    )
    out = await hook(use, ctx)
    assert out is use
    assert captured["tool"] == "ExcelRuntimeEdit"
    assert captured["op_count"] == 1
    assert captured["op_summary"][0]["kind"] == "set_cell"
    assert "ExcelRuntimeEdit" in ctx.scratch.get("approved_tools", set())
    assert "ExcelRuntimeEdit" in ctx.scratch.get("diff_preview_approved", set())


@pytest.mark.asyncio
async def test_hook_rejects_excel_runtime(tmp_path):
    use = ToolUseBlock(
        id="e1",
        name="ExcelRuntimeEdit",
        input={
            "path": "book.xlsx",
            "ops": [{"op": "set_cell", "sheet": "S", "cell": "A1", "value": 1}],
        },
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))

    async def _handler(_p):
        return {"approved": False}

    hook = make_diff_preview_hook(
        {"ExcelRuntimeEdit": _ExcelRuntimeDummy()}, _handler
    )
    out = await hook(use, ctx)
    assert isinstance(out, ToolResultBlock)
    assert out.is_error is True


@pytest.mark.asyncio
async def test_hook_short_circuits_read_only_excel_runtime(tmp_path):
    """Only get_structure → no preview surfaced."""
    use = ToolUseBlock(
        id="e1",
        name="ExcelRuntimeEdit",
        input={"path": "book.xlsx", "ops": [{"op": "get_structure"}]},
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    called = {"n": 0}

    async def _handler(_p):
        called["n"] += 1
        return {"approved": True}

    hook = make_diff_preview_hook(
        {"ExcelRuntimeEdit": _ExcelRuntimeDummy()}, _handler
    )
    out = await hook(use, ctx)
    assert out is use
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_hook_routes_powerpoint_runtime_via_builder(tmp_path):
    use = ToolUseBlock(
        id="p1",
        name="PowerPointRuntimeEdit",
        input={
            "path": "deck.pptx",
            "ops": [
                {
                    "op": "add_text_box",
                    "slide": 1,
                    "text": "Title",
                    "left": 0, "top": 0,
                    "width": 200, "height": 30,
                }
            ],
        },
    )
    ctx = LoopContext(config=LoopConfig(workspace_root=tmp_path))
    captured: dict = {}

    async def _handler(payload):
        captured.update(payload)
        return {"approved": True}

    hook = make_diff_preview_hook(
        {"PowerPointRuntimeEdit": _PowerPointRuntimeDummy()}, _handler
    )
    out = await hook(use, ctx)
    assert out is use
    assert captured["tool"] == "PowerPointRuntimeEdit"
    assert captured["op_summary"][0]["kind"] == "add_text"
