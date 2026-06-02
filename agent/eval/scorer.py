"""Scorers: read a runner's output and produce a ``ScoreResult``.

Each existing runner writes its own summary/result JSON shape, so we
keep concrete scorers small and explicit rather than trying to invent
one universal schema in slice 1.

The ``Scorer`` protocol carries an optional ``verifier`` slot. v1
ignores it. v2 (grader v2 — see docs/conversation.md Phase 2.5
``Grader v2 预留接口`` note) can plug LLM-as-a-Verifier in without
changing call sites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Protocol

from agent.eval.case import ScoreResult


class VerifierProtocol(Protocol):
    """Future best-of-N selector. Slice 1 does not call this."""

    def score_trajectories(self, prompt: str, trajectories: list[dict[str, Any]]) -> int:
        """Return index of the best trajectory in ``trajectories``."""
        ...


class Scorer(Protocol):
    """Read a runner's output file and return a ScoreResult.

    The harness passes ``verifier=None`` in slice 1. Concrete scorers
    may ignore the slot — they only need to consult it when scoring
    multiple trajectories.
    """

    def score(
        self,
        output_path: Path,
        *,
        verifier: Optional[VerifierProtocol] = None,
    ) -> ScoreResult:
        ...


def _load_json(output_path: Path) -> tuple[Any, ScoreResult | None]:
    if not output_path.exists():
        return None, ScoreResult(passed=False, error=f"output not found: {output_path}")
    try:
        return json.loads(output_path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, ScoreResult(passed=False, error=f"json decode failed: {exc}")


class ChecksDictScorer:
    """Score by inspecting the legacy ``checks`` dict.

    Used by p4_word_complex_validation / p4_excel_complex_validation —
    those runners emit ``result.json`` with a ``checks`` dict whose
    truthy/value semantics we evaluate via ``criteria``.

    ``criteria`` is a list of (key_path, predicate) tuples. ``key_path``
    is dotted (``"checks.table_count"``); ``predicate`` takes the
    extracted value and returns True/False.

    A case passes when every criterion is True. Failing criteria are
    listed in ``details["failing_criteria"]``.
    """

    def __init__(self, criteria: list[tuple[str, Callable[[Any], bool]]]):
        self._criteria = list(criteria)

    def score(
        self,
        output_path: Path,
        *,
        verifier: Optional[VerifierProtocol] = None,
    ) -> ScoreResult:
        data, err = _load_json(output_path)
        if err:
            return err
        passing: dict[str, Any] = {}
        failing: dict[str, Any] = {}
        for key_path, predicate in self._criteria:
            value = _dig(data, key_path)
            try:
                ok = bool(predicate(value))
            except Exception as exc:  # predicate error counts as fail
                ok = False
                failing[key_path] = f"<predicate error: {exc}> value={value!r}"
                continue
            if ok:
                passing[key_path] = value
            else:
                failing[key_path] = value
        return ScoreResult(
            passed=not failing,
            details={
                "passing_criteria": list(passing),
                "failing_criteria": failing,
                "criteria_total": len(self._criteria),
            },
        )


class ListSummaryScorer:
    """Score a summary.json that is a *list* of record dicts.

    Used by p4_*_complex_validation, whose top-level JSON is a list of
    per-(scenario, profile) records. The scorer filters records by
    ``scenario`` + ``model_label`` then applies ``ChecksDictScorer``
    semantics to that record.
    """

    def __init__(
        self,
        *,
        scenario: str,
        model_label: str,
        criteria: list[tuple[str, Callable[[Any], bool]]],
    ):
        self._scenario = scenario
        self._model_label = model_label
        self._criteria = list(criteria)

    def score(
        self,
        output_path: Path,
        *,
        verifier: Optional[VerifierProtocol] = None,
    ) -> ScoreResult:
        data, err = _load_json(output_path)
        if err:
            return err
        if not isinstance(data, list):
            return ScoreResult(
                passed=False,
                error=f"expected list summary, got {type(data).__name__}",
            )
        match = None
        for rec in data:
            if not isinstance(rec, dict):
                continue
            if rec.get("scenario") == self._scenario and (
                rec.get("model_label") == self._model_label
                or rec.get("profile") == self._model_label
            ):
                match = rec
        if match is None:
            return ScoreResult(
                passed=False,
                error=(
                    f"no record for scenario={self._scenario!r} "
                    f"model_label={self._model_label!r}"
                ),
                details={"available_records": len(data)},
            )
        passing: list[str] = []
        failing: dict[str, Any] = {}
        for key_path, predicate in self._criteria:
            value = _dig(match, key_path)
            try:
                ok = bool(predicate(value))
            except Exception as exc:
                ok = False
                failing[key_path] = f"<predicate error: {exc}> value={value!r}"
                continue
            if ok:
                passing.append(key_path)
            else:
                failing[key_path] = value
        return ScoreResult(
            passed=not failing,
            details={
                "passing_criteria": passing,
                "failing_criteria": failing,
                "criteria_total": len(self._criteria),
                "scenario": self._scenario,
                "model_label": self._model_label,
                "elapsed_seconds": match.get("elapsed_seconds"),
                "timed_out": match.get("timed_out"),
            },
        )


class PptxLayoutScorer:
    """Score a p11_powerpoint_layout_verifier summary.

    That runner writes ``summary.json`` containing a ``rows`` list,
    each row a (mode, task) pair with ``passed`` already computed.
    """

    def __init__(self, *, mode: str, task: str):
        self._mode = mode
        self._task = task

    def score(
        self,
        output_path: Path,
        *,
        verifier: Optional[VerifierProtocol] = None,
    ) -> ScoreResult:
        data, err = _load_json(output_path)
        if err:
            return err
        if not isinstance(data, dict):
            return ScoreResult(
                passed=False,
                error=f"expected dict summary, got {type(data).__name__}",
            )
        rows = data.get("rows") or []
        match = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("mode") == self._mode and row.get("task") == self._task:
                match = row
                break
        if match is None:
            return ScoreResult(
                passed=False,
                error=f"no row for mode={self._mode!r} task={self._task!r}",
                details={"available_rows": len(rows)},
            )
        return ScoreResult(
            passed=bool(match.get("passed")),
            details={
                "verifier_passed": match.get("verifier_passed"),
                "expected_verifier_passed": match.get("expected_verifier_passed"),
                "warnings_count": len(match.get("warnings") or []),
                "violations_count": len(match.get("violations") or []),
            },
        )


def _dig(data: Any, key_path: str) -> Any:
    """Dotted-path lookup; ``"a.b.c"`` returns data['a']['b']['c'] or None."""
    cur = data
    for part in key_path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur
