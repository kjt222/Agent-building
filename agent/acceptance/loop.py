"""P14.4 long-running task acceptance protocol — stateless policy.

For tasks that span hours / days (KLayout layout iteration, Sentaurus
simulation chain), we can't manually intervene every iteration. This module
is the *policy engine*: given one iteration's verdict plus cumulative stats,
`decide_next_action` returns what the loop should do next — continue,
auto_fix, ask_user, rollback, or stop_converged.

It is deliberately a pure function, not a runner. The runner (smoke script /
agent server / notebook) owns the retry-count bookkeeping and calls
`decide_next_action(spec, verdict, retry_counts)` after each iteration, then
acts on the returned `LoopDecision`. Keeping it stateless makes the policy
trivially testable and reusable across very different execution shells.

Four hard constraints from docs/conversation.md P14.4:

  1. never silently advance — any L1/L2/L3 fail surfaces as auto_fix / rollback
     / ask_user, never a quiet `continue`;
  2. never silently guess on uncertainty — `model_self_confidence` that is not
     a proven `pass` forces ask_user (no auto-fix loop, since the model just
     told us it doesn't know);
  3. bounded autonomy — same-failure auto_fix is bounded by
     `max_auto_fix_retries` (default 3); after that, escalate;
  4. token / clock budget checkpoint — once cumulative tokens or wall clock
     exceed the budget, ask_user even with no failure raised.

Note: this module was reimplemented from its test contract
(tests/unit/test_loop_controller.py) after the D-drive-format recovery — the
session-end source lived in an earlier rotated session and was not captured
in the available transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


# Verdict tags. A check is satisfied only on the exact string "pass"; anything
# else ("fail", "warn", "uncertain", "unknown", "") is treated as not-pass.
Status = str

EscalationOption = Literal["auto_fix", "rollback", "ask_user"]
ActionKind = Literal[
    "continue",
    "auto_fix",
    "ask_user",
    "rollback",
    "stop_converged",
]


@dataclass
class AcceptancePolicy:
    """Which oracle/visual layers gate acceptance, and the convergence streak.

    L2_oracle names the domain oracle for the semantic check; L3 visual checks
    run every `L3_visual_check_every` iterations; the loop is considered
    converged after `converged_after` consecutive all-pass iterations.
    """

    L2_oracle: str = ""
    L3_visual_check_every: int = 1
    converged_after: int = 3


@dataclass
class EscalationPolicy:
    """How to react to failures and resource budgets.

    `on_L1_fail` picks the action for a structural (L1) failure. Budgets are
    opt-in: a `None` budget never triggers a checkpoint.
    """

    on_L1_fail: EscalationOption = "auto_fix"
    max_auto_fix_retries: int = 3
    max_tokens_before_checkin: Optional[int] = None
    max_wall_clock_before_checkin: Optional[float] = None


@dataclass(frozen=True)
class TaskSpec:
    """Immutable description of one long-running acceptance task."""

    user_prompt: str
    expected_outcome: str
    acceptance: AcceptancePolicy = field(default_factory=AcceptancePolicy)
    escalation_policy: EscalationPolicy = field(default_factory=EscalationPolicy)


@dataclass
class IterationVerdict:
    """The graded result of a single loop iteration."""

    iteration: int = 1
    L1_structural: Status = "pass"
    L2_semantic: Status = "pass"
    L3_user_view: Status = "pass"
    model_self_confidence: Status = "pass"
    # Stable key identifying *what* failed, so retry budgets are per-failure.
    failure_key: str = ""
    # Number of consecutive all-pass iterations observed so far (incl. this).
    consecutive_passes: int = 0
    # Cumulative resource counters.
    token_count: int = 0
    wall_clock_seconds: float = 0.0


@dataclass
class LoopDecision:
    """What the loop should do next, plus why and what to record."""

    action: ActionKind
    reason: str
    should_checkpoint: bool = False
    # Echo of the failure key the caller should increment for the next round.
    next_failure_key: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)


def _is_pass(status: Status) -> bool:
    return status == "pass"


def _autofix_or_cap(
    *,
    layer: str,
    failure_key: str,
    escalation: EscalationPolicy,
    retry_counts: dict[str, int],
) -> LoopDecision:
    """Shared bounded-autonomy logic for L1/L2 auto-fixable failures."""
    used = retry_counts.get(failure_key, 0)
    if used >= escalation.max_auto_fix_retries:
        return LoopDecision(
            action="ask_user",
            reason=(
                f"{layer} failure '{failure_key}' hit the auto-fix retry cap "
                f"({escalation.max_auto_fix_retries}); escalating to user."
            ),
            should_checkpoint=True,
            next_failure_key=failure_key,
            details={"attempts": used},
        )
    return LoopDecision(
        action="auto_fix",
        reason=f"{layer} failure '{failure_key}'; attempting auto-fix.",
        next_failure_key=failure_key,
        details={"attempts": used + 1},
    )


def decide_next_action(
    spec: TaskSpec,
    verdict: IterationVerdict,
    retry_counts: Optional[dict[str, int]] = None,
) -> LoopDecision:
    """Pure policy: map (spec, verdict, retry_counts) -> LoopDecision.

    Precedence: structural/semantic/visual failures first (constraint 1),
    then resource budgets (constraint 4, checked before uncertainty), then
    model uncertainty (constraint 2), then convergence, else continue.
    """
    retry_counts = retry_counts or {}
    esc = spec.escalation_policy

    # --- Constraint 1: never silently advance on a failure -----------------
    if not _is_pass(verdict.L1_structural):
        if esc.on_L1_fail == "rollback":
            return LoopDecision(
                action="rollback",
                reason="L1 structural failure; rolling back to last good state.",
                should_checkpoint=True,
                next_failure_key=verdict.failure_key or None,
            )
        if esc.on_L1_fail == "ask_user":
            return LoopDecision(
                action="ask_user",
                reason="L1 structural failure; policy escalates to user.",
                next_failure_key=verdict.failure_key or None,
            )
        return _autofix_or_cap(
            layer="L1",
            failure_key=verdict.failure_key,
            escalation=esc,
            retry_counts=retry_counts,
        )

    if not _is_pass(verdict.L2_semantic):
        return _autofix_or_cap(
            layer="L2",
            failure_key=verdict.failure_key,
            escalation=esc,
            retry_counts=retry_counts,
        )

    if not _is_pass(verdict.L3_user_view):
        # warn or fail — the oracle isn't happy enough to advance.
        return LoopDecision(
            action="ask_user",
            reason=(
                f"L3 user-view check returned '{verdict.L3_user_view}'; "
                "asking the user before advancing."
            ),
            next_failure_key=verdict.failure_key or None,
        )

    # --- Constraint 4: budget checkpoint (before uncertainty) --------------
    if (
        esc.max_tokens_before_checkin is not None
        and verdict.token_count >= esc.max_tokens_before_checkin
    ):
        return LoopDecision(
            action="ask_user",
            reason=(
                f"token budget reached ({verdict.token_count} >= "
                f"{esc.max_tokens_before_checkin}); checking in with user."
            ),
            should_checkpoint=True,
            details={"token_count": verdict.token_count},
        )
    if (
        esc.max_wall_clock_before_checkin is not None
        and verdict.wall_clock_seconds >= esc.max_wall_clock_before_checkin
    ):
        return LoopDecision(
            action="ask_user",
            reason=(
                f"wall-clock budget reached ({verdict.wall_clock_seconds}s >= "
                f"{esc.max_wall_clock_before_checkin}s); checking in with user."
            ),
            should_checkpoint=True,
            details={"wall_clock_seconds": verdict.wall_clock_seconds},
        )

    # --- Constraint 2: never silently guess on uncertainty -----------------
    if not _is_pass(verdict.model_self_confidence):
        confidence = verdict.model_self_confidence
        if confidence == "fail":
            return LoopDecision(
                action="ask_user",
                reason="model reported low self-confidence (fail); checkpointing.",
                should_checkpoint=True,
            )
        # "uncertain", "unknown", or anything else not proven pass.
        return LoopDecision(
            action="ask_user",
            reason=(
                f"model self-confidence is '{confidence}' (not a proven pass); "
                "uncertain, so asking the user rather than guessing."
            ),
        )

    # --- Convergence -------------------------------------------------------
    if verdict.consecutive_passes >= spec.acceptance.converged_after:
        return LoopDecision(
            action="stop_converged",
            reason=(
                f"{verdict.consecutive_passes} consecutive all-pass iterations "
                f">= converged_after ({spec.acceptance.converged_after})."
            ),
        )

    return LoopDecision(
        action="continue",
        reason="all checks pass; convergence streak not yet reached.",
    )
