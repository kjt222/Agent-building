"""Slice 1: registry builds, every case has required fields, no dupes."""

from __future__ import annotations

import pytest

from agent.eval import build_registry, build_tier_a, get_case


def test_tier_a_has_at_least_five_cases():
    cases = build_tier_a()
    assert len(cases) >= 5, [c.id for c in cases]


def test_every_case_has_required_fields():
    for case in build_tier_a():
        assert case.id
        assert case.suite == "tier_a"
        assert case.title
        assert case.invocation.runner_path.exists(), (
            f"runner missing for {case.id}: {case.invocation.runner_path}"
        )
        assert callable(case.scorer_factory)
        # scorer_factory must produce a Scorer with a `score` method.
        scorer = case.scorer_factory()
        assert hasattr(scorer, "score")


def test_case_ids_unique():
    ids = [c.id for c in build_tier_a()]
    assert len(ids) == len(set(ids)), ids


def test_build_registry_all_includes_every_tier():
    a = {c.id for c in build_tier_a()}
    b = {c.id for c in build_registry("all")}
    assert a.issubset(b)
    # Once Tier B is registered, ``all`` is a proper superset of Tier A.
    from agent.eval.registry import build_tier_b
    tb = {c.id for c in build_tier_b()}
    assert b == a | tb


def test_get_case_lookup_and_miss():
    case = get_case("p4_word_thesis_all_in_one")
    assert case.title.startswith("Word")
    with pytest.raises(KeyError):
        get_case("no-such-case")


def test_unknown_suite_raises():
    with pytest.raises(ValueError):
        build_registry("tier_z")


def test_word_cases_target_both_models():
    word_cases = [c for c in build_tier_a() if c.id.startswith("p4_word_")]
    assert word_cases
    for c in word_cases:
        assert "doubao-code" in c.suggested_models
        assert "gpt-5.5" in c.suggested_models
