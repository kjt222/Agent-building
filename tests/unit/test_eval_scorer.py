"""Slice 1: ChecksDictScorer / ListSummaryScorer / PptxLayoutScorer."""

from __future__ import annotations

import json
from pathlib import Path

from agent.eval.scorer import (
    ChecksDictScorer,
    ListSummaryScorer,
    PptxLayoutScorer,
    _dig,
)


def _write(tmp_path: Path, name: str, payload) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_dig_returns_none_on_miss():
    assert _dig({"a": {"b": 1}}, "a.b") == 1
    assert _dig({"a": {"b": 1}}, "a.c") is None
    assert _dig({"a": 1}, "a.b") is None
    assert _dig([1, 2], "a") is None


def test_checks_dict_scorer_passes_when_all_criteria_met(tmp_path):
    p = _write(tmp_path, "r.json", {"checks": {"table_count": 2, "footnotes_part": True}})
    scorer = ChecksDictScorer([
        ("checks.table_count", lambda v: isinstance(v, int) and v >= 1),
        ("checks.footnotes_part", lambda v: v is True),
    ])
    r = scorer.score(p)
    assert r.passed is True
    assert r.details["failing_criteria"] == {}
    assert r.details["criteria_total"] == 2


def test_checks_dict_scorer_lists_failing(tmp_path):
    p = _write(tmp_path, "r.json", {"checks": {"table_count": 0}})
    scorer = ChecksDictScorer([
        ("checks.table_count", lambda v: isinstance(v, int) and v >= 1),
        ("checks.footnotes_part", lambda v: v is True),
    ])
    r = scorer.score(p)
    assert r.passed is False
    assert "checks.table_count" in r.details["failing_criteria"]
    assert "checks.footnotes_part" in r.details["failing_criteria"]


def test_checks_dict_scorer_handles_missing_file(tmp_path):
    r = ChecksDictScorer([]).score(tmp_path / "missing.json")
    assert r.passed is False
    assert "output not found" in r.error


def test_list_summary_scorer_selects_record_by_scenario_and_model(tmp_path):
    p = _write(tmp_path, "s.json", [
        {"scenario": "A", "model_label": "m1", "timed_out": False,
         "checks": {"table_count": 1, "heading_styles": {"H1": "Heading 1",
                                                          "H2": "Heading 2",
                                                          "H3": "Heading 3",
                                                          "H4": "Heading 4"}}},
        {"scenario": "A", "model_label": "m2", "timed_out": True,
         "checks": {"table_count": 0}},
    ])
    scorer = ListSummaryScorer(scenario="A", model_label="m1", criteria=[
        ("timed_out", lambda v: v is False),
        ("checks.table_count", lambda v: v >= 1),
        ("checks.heading_styles", lambda v: isinstance(v, dict) and len(v) >= 4),
    ])
    r = scorer.score(p)
    assert r.passed is True
    assert r.details["scenario"] == "A"
    assert r.details["model_label"] == "m1"


def test_list_summary_scorer_missing_record(tmp_path):
    p = _write(tmp_path, "s.json", [{"scenario": "A", "model_label": "m1"}])
    scorer = ListSummaryScorer(scenario="Z", model_label="m1", criteria=[])
    r = scorer.score(p)
    assert r.passed is False
    assert "no record for scenario=" in r.error


def test_list_summary_scorer_rejects_non_list(tmp_path):
    p = _write(tmp_path, "s.json", {"not": "a list"})
    r = ListSummaryScorer(scenario="A", model_label="m", criteria=[]).score(p)
    assert r.passed is False
    assert "expected list summary" in r.error


def test_list_summary_scorer_falls_back_to_profile_when_model_label_absent(tmp_path):
    p = _write(tmp_path, "s.json", [
        {"scenario": "A", "profile": "doubao-code", "timed_out": False, "checks": {}},
    ])
    scorer = ListSummaryScorer(scenario="A", model_label="doubao-code", criteria=[
        ("timed_out", lambda v: v is False),
    ])
    r = scorer.score(p)
    assert r.passed is True


def test_pptx_layout_scorer_matches_row(tmp_path):
    p = _write(tmp_path, "s.json", {
        "passed": True,
        "rows": [
            {"mode": "deterministic", "task": "alpha", "passed": True,
             "verifier_passed": True, "expected_verifier_passed": True,
             "warnings": ["w1"], "violations": []},
            {"mode": "deterministic", "task": "beta", "passed": False,
             "verifier_passed": False, "expected_verifier_passed": True,
             "warnings": [], "violations": ["v1"]},
        ],
    })
    r = PptxLayoutScorer(mode="deterministic", task="alpha").score(p)
    assert r.passed is True
    assert r.details["warnings_count"] == 1
    assert r.details["violations_count"] == 0

    r2 = PptxLayoutScorer(mode="deterministic", task="beta").score(p)
    assert r2.passed is False
    assert r2.details["violations_count"] == 1


def test_pptx_layout_scorer_missing_row(tmp_path):
    p = _write(tmp_path, "s.json", {"rows": []})
    r = PptxLayoutScorer(mode="x", task="y").score(p)
    assert r.passed is False
    assert "no row" in r.error
