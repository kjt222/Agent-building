"""Slice 1: EvalCase + ScoreResult + CaseResult dataclasses."""

from __future__ import annotations

from pathlib import Path

from agent.eval import EvalCase, ScoreResult
from agent.eval.case import CaseResult, Invocation


def _stub_scorer():
    class _Stub:
        def score(self, output_path, *, verifier=None):
            return ScoreResult(passed=True, details={"stub": True})
    return _Stub()


def test_eval_case_is_immutable():
    case = EvalCase(
        id="x",
        suite="tier_a",
        title="t",
        prompt_summary="p",
        invocation=Invocation(runner_path=Path("/tmp/x.py")),
        output_path_template="{artifact_root}/x.json",
        scorer_factory=_stub_scorer,
    )
    import dataclasses
    assert dataclasses.is_dataclass(case)
    try:
        case.id = "y"
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("EvalCase should be frozen")


def test_score_result_defaults():
    r = ScoreResult(passed=True)
    assert r.passed is True
    assert r.score is None
    assert r.details == {}
    assert r.error == ""


def test_case_result_round_trip():
    r = CaseResult(
        case_id="abc",
        model="doubao-code",
        started_at="2026-05-16T10:00:00",
        elapsed_s=12.5,
        output_path=Path("/tmp/o.json"),
        score=ScoreResult(passed=False, error="boom"),
        invocation_cmd=("python", "x.py"),
    )
    assert r.case_id == "abc"
    assert r.score.error == "boom"
    assert r.invocation_cmd == ("python", "x.py")
