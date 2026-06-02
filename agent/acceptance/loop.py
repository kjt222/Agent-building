"""P14.4 long-running task acceptance protocol.

For tasks that span hours / days (KLayout layout iteration, Sentaurus
simulation chain), we can't manually intervene every iteration. This module
provides the *policy engine* — given an iteration's verdict + cumulative
stats, decide what the loop should do next: continue, auto-fix, escalate to
user, stop converged, or stop failed.

It is intentionally NOT a runner. The runner (smoke script / agent server /
notebook) calls `LoopController.next_action(...)` after each iteration and
acts on the returned `LoopAction`. This lets the same policy be reused
across very different execution shells.

Four hard constraints from docs/conversation.md P14.4:

  1. never silently advance — any L1/L2/L3 fail surfaces in `next_action`
     output as either a retry instruction with a `repair_hint`, or an
     `escalate_to_user` action; never `continue` with a `fail` quietly
  2. never silently guess on uncertainty — `model_self_confidence ==
     "uncertain"` forces escalate_to_user (no auto-fix loop, since the
     model just told us it doesn't know what to do)
  3. bounded autonomy — same-failure auto_fix bounded by
     `max_auto_fix_retries` (default 3); after that escalate
  4. token / clock budget checkpoint — once cumulative tokens or wall
     clock exceeds the budget, emit `escalate_to_user` even when no failure
     was raised, dump state, let user say go-no-go
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


EscalationOption = Literal["auto_fix", "rollback", "ask_user"]


@dataclass
class EscalationPolicy:
    on_L1_fail: EscalationOption = "auto_fix"
    on_L2_fail: EscalationOption = "auto_fix"
    on_L3_fail: EscalationOption = "ask_user"
    on_model_uncertain: EscalationOption = "ask_user"
    max_auto_fix_retries: int = 3
    max_tokens_before_checkin: int = 500_000
    max_wall_clock_before_checkin: float = 3600.0  # seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "on_L1_fail": self.on_L1_fail,
            "on_L2_fail": self.on_L2_fail,
            "on_L3_fail": self.on_L3_fail,
            "on_model_uncertain": self.on_model_uncertain,
            "max_auto_fix_retries": self.max_auto_fix_retries,
            "max_tokens_before_checkin": self.max_tokens_before_checkin,
            "max_wall_clock_before_checkin": self.max_wall_clock_before_checkin,
        }


@dataclass
class AcceptanceCriteria:
    """Per-task acceptance config — fed into the loop alongside the prompt."""

    L1_checks: list[str] = field(default_factory=list)  # e.g. ["files_exist", "gds_valid"]
    L2_oracle: str | None = None                        # oracle registry name
    L3_visual_check_every: int = 0                      # 0 = never; N = every N iterations
    converged_after: int = 3                            # all-pass iters needed to stop


@dataclass
class TaskSpec:
    """The contract a long-running task is launched with.

    Long-running runners (KLayout / Sentaurus / future) must populate this
    BEFORE the first iteration. Missing fields force conservative defaults
    so the loop can't "drift" into silent failure modes.
    """

    user_prompt: str
    expected_outcome: str
    acceptance: AcceptanceCriteria = field(default_factory=AcceptanceCriteria)
    escalation_policy: EscalationPolicy = field(default_factory=EscalationPolicy)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_prompt": self.user_prompt,
            "expected_outcome": self.expected_outcome,
            "acceptance": {
                "L1_checks": list(self.acceptance.L1_checks),
                "L2_oracle": self.acceptance.L2_oracle,
                "L3_visual_check_every": self.acceptance.L3_visual_check_every,
                "converged_after": self.acceptance.converged_after,
            },
            "escalation_policy": self.escalation_policy.to_dict(),
        }


Verdict = Literal["pass", "fail", "warn", "unknown"]
SelfConfidence = Literal["pass", "uncertain", "fail", "unknown"]


@dataclass
class IterationResult:
    iteration: int
    L1: Verdict = "unknown"
    L2: Verdict = "unknown"
    L3: Verdict = "unknown"
    model_self_confidence: SelfConfidence = "unknown"
    tokens_used_this_iter: int = 0
    wall_clock_this_iter: float = 0.0
    failure_signature: str | None = None
    """Stable key that distinguishes failure modes — e.g. "L1:files_missing"
    or "L2:overlap". Used to count same-failure repeats for the bounded
    autonomy constraint."""

    def is_all_pass(self) -> bool:
        return self.L1 == "pass" and self.L2 == "pass" and self.L3 == "pass"

    def first_failure(self) -> Literal["L1", "L2", "L3", None]:
        if self.L1 == "fail":
            return "L1"
        if self.L2 == "fail":
            return "L2"
        if self.L3 == "fail":
            return "L3"
        return None


ActionKind = Literal[
    "continue",            # next iteration; nothing to repair
    "auto_fix",            # next iteration, with repair_hint from oracle/judge
    "rollback",            # discard this iter, retry from prior state
    "escalate_to_user",    # stop the loop, wait for user input
    "stop_converged",      # all-pass for N iters, exit success
    "stop_failed",         # unrecoverable, exit failure
]


@dataclass
class LoopAction:
    action: ActionKind
    reason: str
    repair_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "repair_hint": self.repair_hint,
        }


class LoopController:
    """Stateful policy engine — pass each iteration's result, get an action.

    Tracks cumulative tokens / wall-clock / consecutive passes / same-
    failure-signature retry count internally. One controller per task.
    """

    def __init__(self, spec: TaskSpec) -> None:
        self.spec = spec
        self._total_tokens = 0
        self._total_wall = 0.0
        self._consecutive_passes = 0
        self._same_failure_count: dict[str, int] = {}

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def total_wall(self) -> float:
        return self._total_wall

    def next_action(self, result: IterationResult) -> LoopAction:
        self._total_tokens += result.tokens_used_this_iter
        self._total_wall += result.wall_clock_this_iter

        pol = self.spec.escalation_policy

        # Hard constraint 2: never silently guess on uncertainty.
        if result.model_self_confidence == "uncertain":
            return LoopAction(
                action=_to_action(pol.on_model_uncertain),
                reason="model_self_confidence=uncertain — policy forbids "
                       "silent guess",
                repair_hint="ask the user to disambiguate the aesthetic / "
                            "layout / wording decision",
            )
        if result.model_self_confidence == "fail":
            return LoopAction(
                action="stop_failed",
                reason="model self-reported fail",
            )

        # Hard constraint 4: token / clock budget checkpoint.
        if self._total_tokens >= pol.max_tokens_before_checkin:
            return LoopAction(
                action="escalate_to_user",
                reason=f"cumulative tokens {self._total_tokens} >= "
                       f"budget {pol.max_tokens_before_checkin}",
            )
        if self._total_wall >= pol.max_wall_clock_before_checkin:
            return LoopAction(
                action="escalate_to_user",
                reason=f"cumulative wall-clock {self._total_wall:.0f}s >= "
                       f"budget {pol.max_wall_clock_before_checkin:.0f}s",
            )

        # Hard constraint 1: never silently advance on fail.
        failed_tier = result.first_failure()
        if failed_tier is not None:
            self._consecutive_passes = 0
            sig = result.failure_signature or f"{failed_tier}:unspecified"
            self._same_failure_count[sig] = self._same_failure_count.get(sig, 0) + 1
            count = self._same_failure_count[sig]

            # Hard constraint 3: bounded autonomy.
            if count > pol.max_auto_fix_retries:
                return LoopAction(
                    action="escalate_to_user",
                    reason=f"same failure {sig!r} hit {count}× > "
                           f"max_auto_fix_retries={pol.max_auto_fix_retries}",
                )

            on_fail_attr = f"on_{failed_tier}_fail"
            policy = getattr(pol, on_fail_attr)
            return LoopAction(
                action=_to_action(policy),
                reason=f"{failed_tier} failed ({sig}); retry {count}/"
                       f"{pol.max_auto_fix_retries} per policy {policy!r}",
                repair_hint=f"address {failed_tier} failure: {sig}",
            )

        # No fail. Check converged.
        if result.is_all_pass():
            self._consecutive_passes += 1
            if self._consecutive_passes >= self.spec.acceptance.converged_after:
                return LoopAction(
                    action="stop_converged",
                    reason=f"all-pass for {self._consecutive_passes} consecutive "
                           f"iterations (need {self.spec.acceptance.converged_after})",
                )
        else:
            # warn / unknown — reset converged streak (didn't fail, didn't
            # confirm pass either), continue.
            self._consecutive_passes = 0

        return LoopAction(
            action="continue",
            reason="no fail, no escalation trigger; loop continues",
        )


def _to_action(opt: EscalationOption) -> ActionKind:
    """Translate policy option → loop action verb."""
    if opt == "auto_fix":
        return "auto_fix"
    if opt == "rollback":
        return "rollback"
    return "escalate_to_user"
