"""Tests for the soft-warn Write-on-Excalidraw-canvas behavior (P14.6.12)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from agent.tools_v2.primitives import WriteTool


def _make_ctx(workspace_root: Path) -> SimpleNamespace:
    """Minimal LoopContext-shaped object the WriteTool needs.

    Mirrors the surface WriteTool / _resolve_guarded_path touch:
    ctx.scratch (dict), ctx.config.workspace_root,
    ctx.config.bash_backend, ctx.config.local_bash_policy.
    """
    config = SimpleNamespace(
        workspace_root=str(workspace_root),
        bash_backend="local",
        local_bash_policy="trusted",
        access_mode="full-access",
    )
    return SimpleNamespace(
        scratch={"workspace_root": str(workspace_root)},
        config=config,
        access_mode="full-access",
    )


def _run(coro):
    # Fresh loop per call: the shared get_event_loop() can be left closed by
    # an earlier async test in the full-suite ordering, which made these
    # tests spuriously fail depending on collection order.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _fence_content() -> str:
    return (
        "---\nexcalidraw-plugin: parsed\n---\n# Excalidraw Data\n\n"
        "## Drawing\n```compressed-json\nNoIgAA...\n```\n"
    )


def test_write_to_excalidraw_md_emits_warning(tmp_path):
    p = tmp_path / "drawing.excalidraw.md"
    tool = WriteTool()
    ctx = _make_ctx(tmp_path)
    # WriteTool requires the file be "read" first if it exists; for a
    # brand-new path that check is skipped — so we test the brand-new
    # case here.
    res = _run(tool.run({"path": str(p), "content": _fence_content()}, ctx))
    assert res.is_error is False
    assert "WARNING" in res.content
    assert "Excalidraw" in res.content
    assert "obsidian_write_excalidraw_elements" in res.content


def test_write_to_plain_md_with_fence_emits_warning(tmp_path):
    p = tmp_path / "note.md"
    p.write_text(_fence_content(), encoding="utf-8")
    tool = WriteTool()
    ctx = _make_ctx(tmp_path)
    # Mark as read so WriteTool allows overwrite
    ctx.scratch["read_files"] = {str(p.resolve())}
    res = _run(tool.run({"path": str(p), "content": _fence_content()}, ctx))
    assert res.is_error is False
    assert "WARNING" in res.content
    assert "Excalidraw" in res.content


def test_write_to_non_excalidraw_md_no_warning(tmp_path):
    p = tmp_path / "regular.md"
    tool = WriteTool()
    ctx = _make_ctx(tmp_path)
    res = _run(tool.run({"path": str(p), "content": "# Hello\n\njust text\n"}, ctx))
    assert res.is_error is False
    assert "WARNING" not in res.content


def test_write_destroying_fence_warns_about_destruction(tmp_path):
    """If the model writes new content WITHOUT a fence to an existing
    Excalidraw canvas, the warning should explicitly flag that it will
    destroy every element."""
    p = tmp_path / "note.md"
    p.write_text(_fence_content(), encoding="utf-8")
    tool = WriteTool()
    ctx = _make_ctx(tmp_path)
    ctx.scratch["read_files"] = {str(p.resolve())}
    res = _run(tool.run(
        {"path": str(p), "content": "# Replaced\n\nno fence here\n"}, ctx
    ))
    assert res.is_error is False
    assert "WARNING" in res.content
    assert "destroy" in res.content.lower()
