"""Eval-harness data types — pure dataclasses, no I/O, no execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Invocation:
    """Describes how to launch the underlying runner via subprocess.

    Fields use ``{model}``, ``{artifact_root}``, ``{base_url}`` as format
    placeholders. The runner module fills them at execution time.
    """

    runner_path: Path
    args: tuple[str, ...] = ()
    needs_base_url: bool = True
    timeout_s: float = 600.0


@dataclass(frozen=True)
class EvalCase:
    """Static metadata for one eval case.

    A case is the smallest scorable unit. One legacy runner can expose
    several cases (e.g. ``thesis_all_in_one`` and ``thesis_review_fix``
    are two cases of the same runner).
    """

    id: str
    suite: str
    title: str
    prompt_summary: str
    invocation: Invocation
    output_path_template: str
    scorer_factory: Callable[[], "Scorer"]
    suggested_models: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class ScoreResult:
    """Result of scoring one (case, output) pair.

    ``score`` is reserved for future best-of-N aggregation (see
    LLM-as-a-Verifier grader v2 note in docs/conversation.md). v1 of the
    harness only consults ``passed``.
    """

    passed: bool
    score: Optional[float] = None
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class CaseResult:
    """Full execution + scoring result for one case × model run."""

    case_id: str
    model: str
    started_at: str
    elapsed_s: float
    output_path: Path | None
    score: ScoreResult
    invocation_cmd: tuple[str, ...] = ()
    stdout_tail: str = ""
    stderr_tail: str = ""


