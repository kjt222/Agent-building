"""Tests for agent.core.file_search (P12.5 @file mention)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.core.file_search import (
    format_attached_files_block,
    parse_attached_files,
    search_files,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Build a tiny workspace with files, dirs, and ignored locations."""

    (tmp_path / "agent" / "core").mkdir(parents=True)
    (tmp_path / "agent" / "core" / "loop.py").write_text("# loop", encoding="utf-8")
    (tmp_path / "agent" / "core" / "hooks.py").write_text("# hooks", encoding="utf-8")
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_loop.py").write_text("# t", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "implementation.md").write_text("# i", encoding="utf-8")
    (tmp_path / "README.md").write_text("# r", encoding="utf-8")
    # ignored
    (tmp_path / ".venv" / "site-packages").mkdir(parents=True)
    (tmp_path / ".venv" / "site-packages" / "loop.py").write_text("# v", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "stale.pyc").write_text("x", encoding="utf-8")
    (tmp_path / "tests" / "results" / "stale-run").mkdir(parents=True)
    (tmp_path / "tests" / "results" / "stale-run" / "summary.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / ".hiddendir").mkdir()
    (tmp_path / ".hiddendir" / "secret.txt").write_text("s", encoding="utf-8")
    return tmp_path


def test_search_prefix_beats_substring(workspace: Path) -> None:
    out = search_files(workspace, "loop")
    paths = [e.path for e in out]
    # loop.py is an exact basename match → rank 0
    assert "agent/core/loop.py" in paths
    # test_loop.py is a substring match → rank 2; should come AFTER loop.py
    assert paths.index("agent/core/loop.py") < paths.index("tests/unit/test_loop.py")


def test_search_skips_venv_pycache_and_hidden(workspace: Path) -> None:
    out = search_files(workspace, "loop")
    paths = [e.path for e in out]
    assert all(".venv" not in p for p in paths), paths
    assert all("__pycache__" not in p for p in paths), paths
    # The .hiddendir entry should never surface.
    assert all(".hiddendir" not in p for p in paths), paths


def test_search_skips_tests_results(workspace: Path) -> None:
    out = search_files(workspace, "summary")
    paths = [e.path for e in out]
    # tests/results/... must be pruned even though it would substring-match.
    assert all(not p.startswith("tests/results") for p in paths), paths


def test_search_empty_query_returns_recent_files(workspace: Path) -> None:
    out = search_files(workspace, "")
    # Empty query: we should get back something (recent files) and we should
    # respect ignore rules.
    assert len(out) > 0
    assert all(".venv" not in e.path for e in out)


def test_search_respects_limit(workspace: Path) -> None:
    # Create 30 .py files to ensure the limit kicks in.
    for i in range(30):
        (workspace / f"f{i}.py").write_text("x", encoding="utf-8")
    out = search_files(workspace, "", limit=5)
    assert len(out) == 5


def test_search_limit_is_clamped_to_hard_max(workspace: Path) -> None:
    out = search_files(workspace, "", limit=10_000)
    # Hard limit is 50; we only created ~5 files so the cap is not stressed,
    # but the function must not crash with absurd input.
    assert 0 < len(out) <= 50


def test_search_returns_relative_forward_slash_paths(workspace: Path) -> None:
    out = search_files(workspace, "implementation")
    assert out
    for entry in out:
        assert "\\" not in entry.path, entry.path
        assert not Path(entry.path).is_absolute()


# --- parse_attached_files -------------------------------------------------


def test_parse_attached_files_basic(workspace: Path) -> None:
    text = "请看一下 @agent/core/loop.py 这个文件"
    out = parse_attached_files(text, workspace)
    assert len(out) == 1
    assert out[0].endswith(os.path.join("agent", "core", "loop.py"))


def test_parse_attached_files_strips_trailing_punctuation(workspace: Path) -> None:
    text = "Open @agent/core/loop.py, then run the test."
    out = parse_attached_files(text, workspace)
    assert len(out) == 1, out
    assert out[0].endswith("loop.py")


def test_parse_attached_files_dedupes(workspace: Path) -> None:
    text = "@README.md and @README.md again"
    out = parse_attached_files(text, workspace)
    assert len(out) == 1


def test_parse_attached_files_rejects_escape(workspace: Path) -> None:
    # `..` escape should be rejected even if the resolved file exists.
    parent = workspace.parent
    (parent / "outside.txt").write_text("o", encoding="utf-8")
    text = "look at @../outside.txt"
    out = parse_attached_files(text, workspace)
    assert out == []


def test_parse_attached_files_rejects_nonexistent(workspace: Path) -> None:
    text = "@does/not/exist.py"
    out = parse_attached_files(text, workspace)
    assert out == []


def test_parse_attached_files_skips_email_at_signs(workspace: Path) -> None:
    # An @ inside an email address must NOT be parsed as a path token.
    text = "Email me at user@example.com"
    out = parse_attached_files(text, workspace)
    assert out == []


def test_parse_attached_files_skips_ignored_locations(workspace: Path) -> None:
    text = "@.venv/site-packages/loop.py and @__pycache__/stale.pyc"
    out = parse_attached_files(text, workspace)
    assert out == []


def test_parse_attached_files_handles_multiple(workspace: Path) -> None:
    text = "Compare @agent/core/loop.py with @agent/core/hooks.py"
    out = parse_attached_files(text, workspace)
    assert len(out) == 2
    assert any(p.endswith("loop.py") for p in out)
    assert any(p.endswith("hooks.py") for p in out)


# --- format_attached_files_block ----------------------------------------


def test_format_block_empty_returns_empty_string() -> None:
    assert format_attached_files_block([]) == ""


def test_format_block_renders_paths() -> None:
    block = format_attached_files_block(["/abs/one.txt", "/abs/two.py"])
    assert block.startswith("<attached_files>")
    assert block.endswith("</attached_files>")
    assert "/abs/one.txt" in block
    assert "/abs/two.py" in block
    # Guidance line should be present so the model knows what to do with them.
    assert "load-bearing" in block.lower() or "explicitly attached" in block.lower()
