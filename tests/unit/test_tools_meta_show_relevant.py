"""Unit tests for agent/tools_meta/show_relevant_tools.py (P14.6.5)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.tools_meta.show_relevant_tools import show_relevant_tools_sync


def _names(suggestions) -> list[str]:
    return [s.name for s in suggestions]


def test_obsidian_keywords_point_at_skill_not_tool():
    """P14.6.16: no dedicated obsidian_* tool — catalog returns a skill
    pointer the model must read."""
    out = show_relevant_tools_sync("insert formula into Obsidian Excalidraw canvas")
    names = _names(out)
    assert "__skill__obsidian-excalidraw" in names
    assert not any(n.startswith("obsidian_") for n in names)


def test_chinese_keyword_公式推导_matches_obsidian():
    """The catalog includes Chinese keywords too."""
    out = show_relevant_tools_sync("在画板里加公式推导")
    names = _names(out)
    assert "__skill__obsidian-excalidraw" in names


def test_endnote_task_returns_word_runtime_tools():
    out = show_relevant_tools_sync("把 docx 的 endnote 引用改成注脚")
    names = _names(out)
    assert "WordRuntimeEdit" in names
    assert "WordRead" in names


def test_klayout_task_returns_klayout_tool():
    out = show_relevant_tools_sync("draw a GDS layout with KLayout")
    names = _names(out)
    assert "KLayout" in names


def test_unmatched_task_falls_back_to_bash():
    out = show_relevant_tools_sync("zzz xyzzy nonsense")
    names = _names(out)
    assert names == ["Bash"]
    assert "Read/Write/Edit/Glob/Grep" in out[0].description


def test_empty_task_summary_falls_back_to_bash():
    out = show_relevant_tools_sync("")
    assert _names(out) == ["Bash"]


def test_no_duplicate_suggestions_when_multiple_keywords_match():
    """A task summary that triggers two catalog rows shouldn't return
    the same tool twice."""
    out = show_relevant_tools_sync("verify an Obsidian canvas oracle")
    names = _names(out)
    assert len(names) == len(set(names))
