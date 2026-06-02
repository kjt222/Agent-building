"""Slice 2/3: markdown report writer."""

from __future__ import annotations

from pathlib import Path

from agent.eval.baseline import BaselineDiff
from agent.eval.case import CaseResult, ScoreResult
from agent.eval.report import write_comparison_md


def _result(case_id: str, model: str, *, passed: bool, err: str = "") -> CaseResult:
    return CaseResult(
        case_id=case_id, model=model,
        started_at="2026-05-16T00:00:00",
        elapsed_s=4.2, output_path=Path("/x"),
        score=ScoreResult(passed=passed, error=err),
        invocation_cmd=("python", "x.py"),
    )


def test_report_renders_matrix_and_totals(tmp_path):
    results = [
        _result("a", "doubao-code", passed=True),
        _result("a", "gpt-5.5", passed=False, err="boom"),
        _result("b", "doubao-code", passed=False),
        _result("b", "gpt-5.5", passed=True),
    ]
    p = write_comparison_md(tmp_path / "r.md", suite="tier_a", results=results)
    text = p.read_text(encoding="utf-8")
    assert "## Pass matrix" in text
    assert "doubao-code" in text
    assert "gpt-5.5" in text
    assert "✅" in text and "❌" in text
    # Per-model totals
    assert "| doubao-code | 1 | 1 | 2 |" in text or "doubao-code | 1 | 1 | 2" in text


def test_report_includes_diff_section_when_provided(tmp_path):
    results = [_result("a", "doubao-code", passed=True)]
    diff = BaselineDiff(new_pass=["a"], new_fail=["b"], missing=["c"])
    p = write_comparison_md(
        tmp_path / "r.md",
        suite="tier_a", results=results, diffs={"doubao-code": diff},
    )
    text = p.read_text(encoding="utf-8")
    assert "## Baseline diff" in text
    assert "New passes" in text and "a" in text
    assert "New failures" in text and "b" in text
    assert "dropped from current run" in text and "c" in text


def test_report_when_no_results_still_renders_header(tmp_path):
    p = write_comparison_md(tmp_path / "r.md", suite="tier_a", results=[])
    text = p.read_text(encoding="utf-8")
    assert "# Eval report" in text
