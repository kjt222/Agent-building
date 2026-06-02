"""Tests for ``agent.core.usage_registry`` (P12.6)."""

from __future__ import annotations

import pytest

from agent.core import usage_registry as ur


@pytest.fixture(autouse=True)
def _clean():
    ur.reset_all()
    yield
    ur.reset_all()


def test_add_run_returns_run_and_cumulative():
    payload = ur.add_run(
        "c1",
        usage={"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
        model="gpt-5.4",
    )
    assert payload["run"]["total_tokens"] == 300
    assert payload["cumulative"]["total_tokens"] == 300
    # gpt-5.4 is priced; cost should be a float, not None.
    assert isinstance(payload["run"]["cost_usd"], float)
    assert payload["run"]["cost_usd"] > 0


def test_add_run_accumulates_across_calls():
    ur.add_run("c1", usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
    payload = ur.add_run(
        "c1", usage={"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}
    )
    assert payload["cumulative"]["input_tokens"] == 15
    assert payload["cumulative"]["output_tokens"] == 27
    assert payload["cumulative"]["total_tokens"] == 42


def test_per_conversation_isolation():
    ur.add_run("c1", usage={"total_tokens": 10})
    ur.add_run("c2", usage={"total_tokens": 5})
    assert ur.get_cumulative("c1")["total_tokens"] == 10
    assert ur.get_cumulative("c2")["total_tokens"] == 5


def test_reset_drops_conversation_only():
    ur.add_run("c1", usage={"total_tokens": 10})
    ur.add_run("c2", usage={"total_tokens": 5})
    ur.reset("c1")
    assert ur.get_cumulative("c1")["total_tokens"] == 0
    assert ur.get_cumulative("c2")["total_tokens"] == 5


def test_unknown_model_returns_no_cost():
    payload = ur.add_run(
        "c1",
        usage={"input_tokens": 100, "output_tokens": 200, "total_tokens": 300},
        model="some-mystery-model",
    )
    assert payload["run"]["cost_usd"] is None
    assert payload["cumulative"]["cost_usd"] is None


def test_flat_rate_model_returns_zero_cost():
    payload = ur.add_run(
        "c1",
        usage={"input_tokens": 1000, "output_tokens": 2000, "total_tokens": 3000},
        model="doubao-seed-2.0-code",
    )
    assert payload["run"]["cost_usd"] == 0.0
    assert payload["cumulative"]["cost_usd"] == 0.0


def test_estimate_cost_explicit():
    # gpt-5.4 = $1.25 in / $10.00 out per 1M tokens
    cost = ur.estimate_cost_usd("gpt-5.4", 1_000_000, 0)
    assert cost == 1.25
    cost = ur.estimate_cost_usd("gpt-5.4", 0, 1_000_000)
    assert cost == 10.0


def test_blank_conversation_id_uses_default_bucket():
    ur.add_run("", usage={"total_tokens": 5})
    ur.add_run(None, usage={"total_tokens": 7})
    assert ur.get_cumulative("default")["total_tokens"] == 12
