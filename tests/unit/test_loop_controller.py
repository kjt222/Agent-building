"""Tests for the P14.4 acceptance loop policy (stateless decision function)."""

from __future__ import annotations

import pytest

from agent.acceptance.loop import (
    AcceptancePolicy,
    EscalationPolicy,
    IterationVerdict,
    LoopDecision,
    TaskSpec,
    decide_next_action,
)


def _spec(**escalation_overrides) -> TaskSpec:
    return TaskSpec(
        user_prompt="run KLayout DRC",
        expected_outcome="zero DRC errors",
        acceptance=AcceptancePolicy(
            L2_oracle="klayout",
            L3_visual_check_every=5,
            converged_after=3,
        ),
        escalation_policy=EscalationPolicy(**escalation_overrides),
    )


def _verdict(**kw) -> IterationVerdict:
    """Default = all-pass + confident, override with kw."""
    defaults = dict(
        iteration=1,
        L1_structural="pass",
        L2_semantic="pass",
        L3_user_view="pass",
        model_self_confidence="pass",
    )
    defaults.update(kw)
    return IterationVerdict(**defaults)


# ---- Constraint 1: never silently advance on fail ----


def test_l1_fail_with_autofix_returns_autofix_action():
    d = decide_next_action(
        _spec(),
        _verdict(L1_structural="fail", failure_key="L1:gds_invalid"),
    )
    assert d.action == "auto_fix"
    assert "L1" in d.reason


def test_l3_fail_always_asks_user_by_default():
    d = decide_next_action(_spec(), _verdict(L3_user_view="fail"))
    assert d.action == "ask_user"
    assert "L3" in d.reason


def test_l3_warn_also_triggers_ask_user():
    """Warn = oracle not happy enough to advance — same path as fail."""
    d = decide_next_action(_spec(), _verdict(L3_user_view="warn"))
    assert d.action == "ask_user"


def test_l2_fail_with_autofix_returns_autofix():
    d = decide_next_action(
        _spec(),
        _verdict(L2_semantic="fail", failure_key="L2:overlap"),
    )
    assert d.action == "auto_fix"


# ---- Constraint 2: never silently guess on uncertainty ----


def test_uncertain_self_confidence_asks_user():
    d = decide_next_action(
        _spec(),
        _verdict(model_self_confidence="uncertain"),
    )
    assert d.action == "ask_user"
    assert "uncertain" in d.reason


def test_self_confidence_fail_asks_user_with_checkpoint():
    d = decide_next_action(
        _spec(),
        _verdict(model_self_confidence="fail"),
    )
    assert d.action == "ask_user"
    assert d.should_checkpoint is True


def test_self_confidence_unknown_treated_as_uncertain():
    """No tag emitted = same as uncertain — caller didn't prove confidence."""
    d = decide_next_action(_spec(), _verdict(model_self_confidence="unknown"))
    assert d.action == "ask_user"


# ---- Constraint 3: bounded autonomy ----


def test_autofix_under_cap_returns_autofix():
    retry = {"L2:overlap": 1}
    d = decide_next_action(
        _spec(max_auto_fix_retries=3),
        _verdict(L2_semantic="fail", failure_key="L2:overlap"),
        retry_counts=retry,
    )
    assert d.action == "auto_fix"
    assert d.details["attempts"] == 2


def test_autofix_at_cap_escalates():
    retry = {"L2:overlap": 3}
    d = decide_next_action(
        _spec(max_auto_fix_retries=3),
        _verdict(L2_semantic="fail", failure_key="L2:overlap"),
        retry_counts=retry,
    )
    assert d.action == "ask_user"
    assert "retry cap" in d.reason


def test_different_failure_keys_have_independent_budgets():
    retry = {"L2:overlap": 3}  # this one exhausted
    d = decide_next_action(
        _spec(max_auto_fix_retries=3),
        _verdict(L2_semantic="fail", failure_key="L2:grouping"),  # different
        retry_counts=retry,
    )
    assert d.action == "auto_fix"  # fresh counter


# ---- Constraint 4: token / clock budget ----


def test_token_budget_asks_user():
    d = decide_next_action(
        _spec(max_tokens_before_checkin=1000),
        _verdict(token_count=1500),
    )
    assert d.action == "ask_user"
    assert "token" in d.reason


def test_wallclock_budget_asks_user():
    d = decide_next_action(
        _spec(max_wall_clock_before_checkin=10),
        _verdict(wall_clock_seconds=15.0),
    )
    assert d.action == "ask_user"
    assert "wall-clock" in d.reason


def test_budget_check_runs_before_uncertainty():
    """Budget checkpoint short-circuits before model-uncertainty path."""
    d = decide_next_action(
        _spec(max_tokens_before_checkin=10),
        _verdict(model_self_confidence="uncertain", token_count=999),
    )
    assert d.action == "ask_user"
    assert "token" in d.reason


# ---- Convergence ----


def test_converges_when_all_pass_and_streak_reached():
    d = decide_next_action(
        _spec(),  # converged_after=3
        _verdict(consecutive_passes=3),
    )
    assert d.action == "stop_converged"


def test_does_not_converge_below_streak():
    d = decide_next_action(_spec(), _verdict(consecutive_passes=2))
    assert d.action == "continue"


def test_continue_when_no_fail_and_not_converged():
    d = decide_next_action(_spec(), _verdict(consecutive_passes=0))
    assert d.action == "continue"


# ---- Policy variants ----


def test_rollback_policy_option():
    d = decide_next_action(
        _spec(on_L1_fail="rollback"),
        _verdict(L1_structural="fail", failure_key="L1:bad"),
    )
    assert d.action == "rollback"
    assert d.should_checkpoint is True


def test_ask_user_policy_for_l1():
    d = decide_next_action(
        _spec(on_L1_fail="ask_user"),
        _verdict(L1_structural="fail", failure_key="L1:bad"),
    )
    assert d.action == "ask_user"


# ---- Frozen dataclass serialization ----


def test_task_spec_is_frozen():
    spec = _spec()
    with pytest.raises(Exception):
        spec.user_prompt = "different"  # type: ignore[misc]


def test_decision_records_failure_key_for_caller_to_increment():
    d = decide_next_action(
        _spec(),
        _verdict(L2_semantic="fail", failure_key="L2:overlap"),
    )
    assert d.next_failure_key == "L2:overlap"


def test_decision_records_attempts_for_caller_to_track():
    d = decide_next_action(
        _spec(max_auto_fix_retries=3),
        _verdict(L1_structural="fail", failure_key="L1:gds"),
        retry_counts={"L1:gds": 0},
    )
    assert d.action == "auto_fix"
    assert d.details["attempts"] == 1
