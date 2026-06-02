"""Slice 2/3: baseline file I/O + diff."""

from __future__ import annotations

import json
from pathlib import Path

from agent.eval.baseline import (
    BaselineEntry,
    baseline_path,
    diff_against,
    dump_baseline,
    load_baseline,
)
from agent.eval.case import CaseResult, ScoreResult


def _result(case_id: str, *, passed: bool) -> CaseResult:
    return CaseResult(
        case_id=case_id, model="doubao-code",
        started_at="2026-05-16T00:00:00",
        elapsed_s=1.0, output_path=None,
        score=ScoreResult(passed=passed),
    )


def test_baseline_path_includes_suite_and_model(tmp_path):
    p = baseline_path(tmp_path, "tier_a", "doubao-code")
    assert p.name == "tier_a_doubao-code.json"


def test_load_baseline_missing_returns_empty(tmp_path):
    assert load_baseline(tmp_path / "no.json") == {}


def test_dump_then_load_roundtrip(tmp_path):
    p = tmp_path / "b.json"
    results = [_result("a", passed=True), _result("b", passed=False)]
    dump_baseline(p, suite="tier_a", model="doubao-code", results=results, note="t")
    loaded = load_baseline(p)
    assert set(loaded) == {"a", "b"}
    assert loaded["a"].passed is True
    assert loaded["b"].passed is False


def test_diff_categorises_changes(tmp_path):
    baseline = {
        "a": BaselineEntry(case_id="a", passed=True),   # was pass, now pass
        "b": BaselineEntry(case_id="b", passed=True),   # was pass, now fail
        "c": BaselineEntry(case_id="c", passed=False),  # was fail, now pass
        "d": BaselineEntry(case_id="d", passed=False),  # was fail, now fail
        "e": BaselineEntry(case_id="e", passed=True),   # gone from current run
    }
    current = [
        _result("a", passed=True),
        _result("b", passed=False),
        _result("c", passed=True),
        _result("d", passed=False),
        _result("f", passed=True),  # new case not in baseline
    ]
    diff = diff_against(current, baseline)
    assert diff.unchanged_pass == ["a"]
    assert diff.new_fail == ["b"]
    assert diff.new_pass == ["c"]
    assert diff.unchanged_fail == ["d"]
    assert diff.missing == ["e"]
    assert diff.new_case == ["f"]
    assert diff.has_regression is True
    assert diff.has_improvement is True


def test_diff_no_regression_no_improvement(tmp_path):
    baseline = {"a": BaselineEntry(case_id="a", passed=True)}
    diff = diff_against([_result("a", passed=True)], baseline)
    assert diff.has_regression is False
    assert diff.has_improvement is False
