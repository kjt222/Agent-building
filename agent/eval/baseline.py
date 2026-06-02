"""Baseline file management.

A baseline file pins the known-good pass/fail outcome for each
(case_id, model) pair. Diffing a fresh run against the baseline tells
us:
  - new_pass:  case was failing before, passes now (progress)
  - new_fail:  case was passing before, fails now (regression)
  - unchanged_pass / unchanged_fail (no signal)
  - missing:   case is in baseline but absent from the current run
  - new_case:  case is in the current run but absent from baseline

Baseline files live under ``tests/eval_baselines/<suite>_<model>.json``
and ARE checked into git so regressions surface in code review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from agent.eval.case import CaseResult


@dataclass
class BaselineEntry:
    case_id: str
    passed: bool
    details: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class BaselineDiff:
    new_pass: list[str] = field(default_factory=list)
    new_fail: list[str] = field(default_factory=list)
    unchanged_pass: list[str] = field(default_factory=list)
    unchanged_fail: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    new_case: list[str] = field(default_factory=list)

    @property
    def has_regression(self) -> bool:
        return bool(self.new_fail)

    @property
    def has_improvement(self) -> bool:
        return bool(self.new_pass)


def baseline_path(results_root: Path, suite: str, model: str) -> Path:
    safe_model = model.replace("/", "_")
    return results_root / f"{suite}_{safe_model}.json"


def load_baseline(path: Path) -> dict[str, BaselineEntry]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"baseline must be a dict, got {type(data).__name__}")
    entries = {}
    for case_id, rec in (data.get("entries") or {}).items():
        if not isinstance(rec, dict):
            continue
        entries[case_id] = BaselineEntry(
            case_id=case_id,
            passed=bool(rec.get("passed")),
            details=rec.get("details") or {},
            error=str(rec.get("error") or ""),
        )
    return entries


def dump_baseline(
    path: Path,
    *,
    suite: str,
    model: str,
    results: Iterable[CaseResult],
    note: str = "",
) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": suite,
        "model": model,
        "note": note,
        "entries": {
            r.case_id: {
                "passed": r.score.passed,
                "details": r.score.details,
                "error": r.score.error,
            }
            for r in results
        },
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def diff_against(
    results: Iterable[CaseResult],
    baseline: dict[str, BaselineEntry],
) -> BaselineDiff:
    diff = BaselineDiff()
    seen: set[str] = set()
    for r in results:
        seen.add(r.case_id)
        prev = baseline.get(r.case_id)
        if prev is None:
            diff.new_case.append(r.case_id)
            continue
        if r.score.passed and not prev.passed:
            diff.new_pass.append(r.case_id)
        elif (not r.score.passed) and prev.passed:
            diff.new_fail.append(r.case_id)
        elif r.score.passed and prev.passed:
            diff.unchanged_pass.append(r.case_id)
        else:
            diff.unchanged_fail.append(r.case_id)
    for case_id in baseline:
        if case_id not in seen:
            diff.missing.append(case_id)
    return diff
