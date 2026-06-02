"""Phase 2.5 eval harness.

Public surface kept intentionally small. Each existing live runner in
``tests/p*/run_*.py`` is wrapped as an ``EvalCase``; the harness drives
them through subprocess + reads their summary/result JSON, then runs a
``Scorer`` to produce a ``ScoreResult``.

The ``Scorer`` protocol takes an optional ``verifier`` slot so that
later slices can plug in best-of-N selection (e.g. LLM-as-a-Verifier).
Slice 1 (Tier A skeleton) does not use the slot.
"""

from agent.eval.case import (
    CaseResult,
    EvalCase,
    Invocation,
    ScoreResult,
)
from agent.eval.registry import build_registry, build_tier_a, build_tier_b, get_case
from agent.eval.scorer import (
    ChecksDictScorer,
    ListSummaryScorer,
    PptxLayoutScorer,
    Scorer,
)

__all__ = [
    "CaseResult",
    "EvalCase",
    "Invocation",
    "ScoreResult",
    "Scorer",
    "ChecksDictScorer",
    "ListSummaryScorer",
    "PptxLayoutScorer",
    "build_tier_a",
    "build_tier_b",
    "build_registry",
    "get_case",
]
