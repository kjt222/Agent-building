"""Tests for the WordRuntimeEdit structured diff builder (P12.2.1)."""

from __future__ import annotations

import pytest

from agent.core.hooks import build_word_runtime_diff, make_diff_preview_hook
from agent.core.loop import (
    LoopConfig,
    LoopContext,
    PermissionLevel,
    ToolResultBlock,
    ToolUseBlock,
)


class _WordRuntimeDummy:
    name = "WordRuntimeEdit"
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False
    description = ""
    input_schema = {}


def _use(ops):
    return ToolUseBlock(
        id="t1",
        name="WordRuntimeEdit",
        input={"path": "report.docx", "ops": ops},
    )


def _ctx():
    return LoopContext(config=LoopConfig())


def test_rename_heading_emits_before_after():
    use = _use([
        {
            "op": "set_heading_text",
            "anchor_heading": "第二章 原理",
            "new_text": "第二章 实验方法",
        }
    ])
    payload = build_word_runtime_diff(use, _ctx())
    assert payload is not None
    assert payload["tool"] == "WordRuntimeEdit"
    assert payload["path"] == "report.docx"
    assert payload["op_count"] == 1
    row = payload["op_summary"][0]
    assert row["kind"] == "rename_heading"
    assert row["before"] == "第二章 原理"
    assert row["after"] == "第二章 实验方法"


def test_insert_under_heading_carries_anchor_and_new_text():
    use = _use([
        {
            "op": "insert_paragraph_after_heading",
            "anchor_heading": "第一章 绪论",
            "new_text": "本章已由智能体更新。",
            "style": "Normal",
        }
    ])
    payload = build_word_runtime_diff(use, _ctx())
    assert payload is not None
    row = payload["op_summary"][0]
    assert row["kind"] == "insert_after_heading"
    assert row["anchor"] == "第一章 绪论"
    assert row["after_text"] == "本章已由智能体更新。"
    assert row["style"] == "Normal"


def test_refresh_fields_is_a_side_effect_row():
    use = _use([{"op": "refresh_fields"}])
    payload = build_word_runtime_diff(use, _ctx())
    assert payload is not None
    row = payload["op_summary"][0]
    assert row["kind"] == "side_effect"
    assert "TOC" in row["summary"] or "fields" in row["summary"].lower()


def test_add_toc_includes_level_range_in_summary():
    use = _use([{"op": "add_toc", "levels": "1-2", "title": "目录"}])
    payload = build_word_runtime_diff(use, _ctx())
    assert payload is not None
    row = payload["op_summary"][0]
    assert row["kind"] == "side_effect"
    assert "1-2" in row["summary"]
    assert "目录" in row["summary"]


def test_read_only_op_returns_none():
    use = _use([{"op": "get_structure"}])
    payload = build_word_runtime_diff(use, _ctx())
    assert payload is None  # nothing to approve


def test_mixed_read_only_and_mutating_keeps_only_mutating_rows():
    use = _use([
        {"op": "get_structure"},
        {
            "op": "set_heading_text",
            "anchor_heading": "第二章 原理",
            "new_text": "第二章 实验方法",
        },
    ])
    payload = build_word_runtime_diff(use, _ctx())
    assert payload is not None
    assert payload["op_count"] == 1
    assert payload["op_summary"][0]["kind"] == "rename_heading"


def test_long_insertion_text_is_truncated_in_summary():
    long_text = "x" * 500
    use = _use([
        {
            "op": "insert_paragraph_after_heading",
            "anchor_heading": "H",
            "new_text": long_text,
        }
    ])
    payload = build_word_runtime_diff(use, _ctx())
    row = payload["op_summary"][0]
    assert "…" in row["summary"]
    # Full text is still preserved in after_text so the model and trace see it.
    assert row["after_text"] == long_text


@pytest.mark.asyncio
async def test_hook_routes_word_runtime_edit_to_structured_preview():
    """End-to-end: the diff hook builds & shows the structured payload."""
    captured: dict = {}

    async def _handler(payload):
        captured.update(payload)
        return {"approved": True}

    use = _use([
        {
            "op": "set_heading_text",
            "anchor_heading": "A",
            "new_text": "B",
        }
    ])
    ctx = _ctx()
    hook = make_diff_preview_hook(
        {"WordRuntimeEdit": _WordRuntimeDummy()}, _handler
    )
    out = await hook(use, ctx)
    assert out is use
    assert captured["tool"] == "WordRuntimeEdit"
    assert captured["op_summary"][0]["kind"] == "rename_heading"
    # Diff approval propagates into the dedicated cache so the standard
    # approval hook does not double-ask.
    assert "WordRuntimeEdit" in ctx.scratch.get("diff_preview_approved", set())


@pytest.mark.asyncio
async def test_hook_short_circuits_when_only_read_only_op():
    called = {"n": 0}

    async def _handler(_p):
        called["n"] += 1
        return {"approved": True}

    use = _use([{"op": "get_structure"}])
    ctx = _ctx()
    hook = make_diff_preview_hook(
        {"WordRuntimeEdit": _WordRuntimeDummy()}, _handler
    )
    out = await hook(use, ctx)
    # No preview surfaced; the call passes straight through.
    assert out is use
    assert called["n"] == 0
