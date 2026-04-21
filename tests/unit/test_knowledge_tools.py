"""Tests for KnowledgeSearchTool / KnowledgeIndexTool against a temp SQLite."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.core.loop import LoopConfig, LoopContext, PermissionLevel
from agent.storage.database import Database
from agent.storage.knowledge_manager import KnowledgeManager
from agent.tools_v2.knowledge_tool import KnowledgeIndexTool, KnowledgeSearchTool


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def kb_ctx(tmp_path: Path) -> LoopContext:
    """LoopContext whose scratch has an isolated KnowledgeManager."""
    db = Database(tmp_path / "kb.db")
    mgr = KnowledgeManager(db=db)
    ctx = LoopContext(config=LoopConfig())
    ctx.scratch["knowledge_manager"] = mgr
    return ctx


@pytest.fixture
def seeded_kb(tmp_path: Path, kb_ctx: LoopContext) -> tuple[LoopContext, Path]:
    """Temp KB directory with two small markdown files indexed under 'demo'."""
    kb_dir = tmp_path / "kb_src"
    kb_dir.mkdir()
    (kb_dir / "alpha.md").write_text(
        "# Alpha\nThe quick brown fox jumps over the lazy dog.\n",
        encoding="utf-8",
    )
    (kb_dir / "beta.md").write_text(
        "# Beta\nPython is a widely used programming language.\n",
        encoding="utf-8",
    )
    mgr: KnowledgeManager = kb_ctx.scratch["knowledge_manager"]
    mgr.index_directory("demo", kb_dir, extensions=[".md"])
    return kb_ctx, kb_dir


# --------------------------------------------------------------------------- #
# KnowledgeSearch
# --------------------------------------------------------------------------- #


def test_search_tool_is_safe_and_parallel():
    t = KnowledgeSearchTool()
    assert t.permission_level == PermissionLevel.SAFE
    assert t.parallel_safe is True


def test_search_action_requires_query(kb_ctx):
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run({"action": "search"}, kb_ctx))
    assert res.is_error is True
    assert "query" in res.content.lower()


def test_list_empty_kb(kb_ctx):
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run({"action": "list"}, kb_ctx))
    assert res.is_error is False
    assert "no knowledge bases" in res.content.lower()


def test_list_after_index(seeded_kb):
    ctx, _ = seeded_kb
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run({"action": "list"}, ctx))
    assert res.is_error is False
    assert "demo" in res.content
    assert "2 files" in res.content


def test_info_unknown_kb_errors(kb_ctx):
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run({"action": "info", "kb_name": "nope"}, kb_ctx))
    assert res.is_error is True


def test_info_requires_kb_name(kb_ctx):
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run({"action": "info"}, kb_ctx))
    assert res.is_error is True


def test_info_after_index(seeded_kb):
    ctx, _ = seeded_kb
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run({"action": "info", "kb_name": "demo"}, ctx))
    assert res.is_error is False
    assert "kb=demo" in res.content
    assert "alpha.md" in res.content and "beta.md" in res.content


def test_search_returns_snippet(seeded_kb):
    ctx, _ = seeded_kb
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run(
        {"action": "search", "query": "Python", "kb_names": ["demo"]}, ctx
    ))
    assert res.is_error is False
    assert "beta.md" in res.content
    assert "Python" in res.content


def test_search_matches_cjk_substrings_with_trigram_tokenizer(kb_ctx, tmp_path):
    kb_dir = tmp_path / "cjk_src"
    kb_dir.mkdir()
    (kb_dir / "note.md").write_text(
        "用RRF效果很好，微纳加工课程材料也能被中文片段检索命中。",
        encoding="utf-8",
    )
    mgr: KnowledgeManager = kb_ctx.scratch["knowledge_manager"]
    mgr.index_directory("cjk", kb_dir, extensions=[".md"])

    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run(
        {"action": "search", "query": "微纳加工", "kb_names": ["cjk"]},
        kb_ctx,
    ))

    assert res.is_error is False
    assert "note.md" in res.content


def test_search_no_match(seeded_kb):
    ctx, _ = seeded_kb
    tool = KnowledgeSearchTool()
    res = asyncio.run(tool.run(
        {"action": "search", "query": "nonexistentxyz123"}, ctx
    ))
    assert res.is_error is False
    assert "no matches" in res.content.lower()


# --------------------------------------------------------------------------- #
# KnowledgeIndex
# --------------------------------------------------------------------------- #


def test_index_tool_needs_approval():
    t = KnowledgeIndexTool()
    assert t.permission_level == PermissionLevel.NEEDS_APPROVAL
    assert t.parallel_safe is False


def test_index_missing_args(kb_ctx):
    tool = KnowledgeIndexTool()
    res = asyncio.run(tool.run({"kb_name": "x"}, kb_ctx))
    assert res.is_error is True
    res = asyncio.run(tool.run({"directory": "./nowhere"}, kb_ctx))
    assert res.is_error is True


def test_index_nonexistent_directory(kb_ctx, tmp_path):
    tool = KnowledgeIndexTool()
    missing = tmp_path / "does_not_exist"
    res = asyncio.run(tool.run(
        {"kb_name": "x", "directory": str(missing)}, kb_ctx
    ))
    assert res.is_error is True
    assert "not found" in res.content.lower()


def test_index_rejects_file_path(kb_ctx, tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hi", encoding="utf-8")
    tool = KnowledgeIndexTool()
    res = asyncio.run(tool.run(
        {"kb_name": "x", "directory": str(f)}, kb_ctx
    ))
    assert res.is_error is True
    assert "not a directory" in res.content.lower()


def test_index_happy_path(kb_ctx, tmp_path):
    kb_dir = tmp_path / "src"
    kb_dir.mkdir()
    (kb_dir / "one.md").write_text("alpha alpha", encoding="utf-8")
    (kb_dir / "two.md").write_text("beta beta", encoding="utf-8")
    tool = KnowledgeIndexTool()
    res = asyncio.run(tool.run(
        {"kb_name": "fresh", "directory": str(kb_dir), "extensions": [".md"]},
        kb_ctx,
    ))
    assert res.is_error is False
    assert "indexed=2" in res.content
    assert "errors=0" in res.content


def test_index_is_idempotent(kb_ctx, tmp_path):
    kb_dir = tmp_path / "src"
    kb_dir.mkdir()
    (kb_dir / "a.md").write_text("hello", encoding="utf-8")
    tool = KnowledgeIndexTool()
    first = asyncio.run(tool.run(
        {"kb_name": "idem", "directory": str(kb_dir), "extensions": [".md"]},
        kb_ctx,
    ))
    assert "indexed=1" in first.content
    second = asyncio.run(tool.run(
        {"kb_name": "idem", "directory": str(kb_dir), "extensions": [".md"]},
        kb_ctx,
    ))
    # Unchanged file → skipped on re-run.
    assert "indexed=0" in second.content
    assert "skipped=1" in second.content
