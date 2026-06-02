"""Tests for the shared P14.1.3 acceptance verdict helper (tests/_acceptance.py)."""

from __future__ import annotations

import sys
from pathlib import Path

# tests/_acceptance.py is a sibling of tests/unit/, not a package member.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _acceptance import (  # noqa: E402
    AcceptanceVerdict,
    build_verdict,
    count_ask_user_questions,
    detect_disclosure,
    parse_self_confidence,
)


# ---- parse_self_confidence ----


def test_parse_self_confidence_pass():
    assert parse_self_confidence("done. <self_confidence>pass</self_confidence>") == "pass"


def test_parse_self_confidence_uncertain():
    assert parse_self_confidence("<self_confidence>uncertain</self_confidence>") == "uncertain"


def test_parse_self_confidence_fail():
    assert parse_self_confidence("oops <self_confidence>fail</self_confidence>") == "fail"


def test_parse_self_confidence_case_insensitive():
    assert parse_self_confidence("<self_confidence>PASS</self_confidence>") == "pass"


def test_parse_self_confidence_last_wins():
    text = (
        "<self_confidence>uncertain</self_confidence>\n"
        "actually rethinking...\n"
        "<self_confidence>pass</self_confidence>"
    )
    assert parse_self_confidence(text) == "pass"


def test_parse_self_confidence_missing_is_unknown():
    assert parse_self_confidence("no tag here") == "unknown"


def test_parse_self_confidence_empty_text():
    assert parse_self_confidence("") == "unknown"


def test_parse_self_confidence_whitespace_only():
    assert parse_self_confidence("   \n\n\t  ") == "unknown"


def test_parse_self_confidence_works_inside_code_fence():
    """Even when the model wraps the tag in ``` we should still parse it.
    The prompt says don't wrap, but be lenient on the read side."""
    text = "All done.\n```\n<self_confidence>pass</self_confidence>\n```\n"
    assert parse_self_confidence(text) == "pass"


def test_parse_self_confidence_tolerates_inner_whitespace():
    assert parse_self_confidence(
        "<self_confidence>\n   uncertain   \n</self_confidence>"
    ) == "uncertain"


def test_parse_self_confidence_ignores_unrelated_xml_lookalikes():
    text = "<confidence>pass</confidence> and <self>uncertain</self>"
    # Neither matches the exact <self_confidence> tag → unknown
    assert parse_self_confidence(text) == "unknown"


def test_build_verdict_auto_parses_self_confidence_from_text():
    """If caller leaves model_self_confidence=unknown, build_verdict parses it."""
    v = build_verdict(
        structural_pass=True,
        semantic_pass=True,
        user_view_pass=True,
        assistant_final_text=(
            "All done. **Unverified items**: open the canvas.\n"
            "<self_confidence>pass</self_confidence>"
        ),
    )
    assert v.model_self_confidence == "pass"
    assert v.overall == "pass"  # tag enables strict pass


def test_build_verdict_explicit_overrides_tag_parse():
    """If caller passes explicit confidence, it takes precedence over text tag."""
    v = build_verdict(
        structural_pass=True,
        semantic_pass=True,
        user_view_pass=True,
        model_self_confidence="fail",  # explicit
        assistant_final_text="<self_confidence>pass</self_confidence>",
    )
    assert v.model_self_confidence == "fail"
    assert v.overall == "fail"


# ---- detect_disclosure ----


def test_detect_disclosure_recognizes_english_unverified_items():
    assert detect_disclosure("All done. **Unverified items**: foo.") == "present"


def test_detect_disclosure_recognizes_chinese_未验证项():
    assert detect_disclosure("已完成。未验证项：请检查。") == "present"


def test_detect_disclosure_recognizes_please_verify():
    assert detect_disclosure("done — please verify visually.") == "present"


def test_detect_disclosure_missing_when_only_strong_claim():
    assert detect_disclosure("All set, complete, done.") == "missing"


def test_detect_disclosure_unknown_when_empty():
    assert detect_disclosure("") == "unknown"


# ---- count_ask_user_questions ----


def test_count_ask_user_questions_basic():
    calls = [
        {"name": "Read"},
        {"name": "AskUserQuestion"},
        {"name": "Write"},
        {"name": "ask_user_question"},
        {"name": "ask user question"},
    ]
    assert count_ask_user_questions(calls) == 3


def test_count_ask_user_questions_empty():
    assert count_ask_user_questions([]) == 0


# ---- overall verdict computation ----


def test_overall_pass_requires_all_tiers_plus_disclosure_plus_confidence():
    v = AcceptanceVerdict(
        L1_structural="pass",
        L2_semantic="pass",
        L3_user_view="pass",
        model_self_confidence="pass",
        disclosure="present",
    )
    assert v.overall == "pass"


def test_overall_pass_accepts_uncertain_when_user_was_asked():
    """If model self-rated uncertain BUT called AskUserQuestion ≥ 1, still pass."""
    v = AcceptanceVerdict(
        L1_structural="pass",
        L2_semantic="pass",
        L3_user_view="pass",
        model_self_confidence="uncertain",
        user_questions_asked=2,
        disclosure="present",
    )
    assert v.overall == "pass"


def test_overall_needs_review_when_model_uncertain_and_no_user_question():
    """The silent-guess failure mode — uncertain without asking."""
    v = AcceptanceVerdict(
        L1_structural="pass",
        L2_semantic="pass",
        L3_user_view="pass",
        model_self_confidence="uncertain",
        user_questions_asked=0,
        disclosure="present",
    )
    assert v.overall == "needs_review"


def test_overall_needs_review_when_disclosure_missing():
    """L1/2/3 all green but model didn't disclose → not pass."""
    v = AcceptanceVerdict(
        L1_structural="pass",
        L2_semantic="pass",
        L3_user_view="pass",
        model_self_confidence="pass",
        disclosure="missing",
    )
    assert v.overall == "needs_review"


def test_overall_fail_when_any_tier_fails():
    v = AcceptanceVerdict(
        L1_structural="pass",
        L2_semantic="fail",
        L3_user_view="unknown",
        model_self_confidence="pass",
        disclosure="present",
    )
    assert v.overall == "fail"


def test_overall_fail_when_model_self_confidence_fail():
    v = AcceptanceVerdict(
        L1_structural="pass",
        L2_semantic="pass",
        L3_user_view="pass",
        model_self_confidence="fail",
        disclosure="present",
    )
    assert v.overall == "fail"


def test_overall_partial_when_l1_pass_l2_l3_unknown_with_disclosure():
    """The CURRENT runner state for P13.2.3 — L1 passes, L2/L3 oracles not yet
    built. Should be `partial`, distinguishing it from L1-failed `needs_review`."""
    v = AcceptanceVerdict(
        L1_structural="pass",
        L2_semantic="unknown",
        L3_user_view="unknown",
        model_self_confidence="pass",
        disclosure="present",
    )
    assert v.overall == "partial"


def test_overall_needs_review_when_l1_unknown():
    """Without even L1 pass, no claim to anything."""
    v = AcceptanceVerdict(
        L1_structural="unknown",
        disclosure="present",
        model_self_confidence="pass",
    )
    assert v.overall == "needs_review"


# ---- build_verdict convenience ----


def test_build_verdict_wires_everything_through():
    v = build_verdict(
        structural_pass=True,
        semantic_pass=None,  # → unknown
        user_view_pass=None,
        model_self_confidence="uncertain",
        assistant_final_text="Done. **Unverified items**: open the canvas.",
        tool_calls=[{"name": "Write"}, {"name": "AskUserQuestion"}],
    )
    assert v.L1_structural == "pass"
    assert v.L2_semantic == "unknown"
    assert v.L3_user_view == "unknown"
    assert v.disclosure == "present"
    assert v.user_questions_asked == 1
    assert v.model_self_confidence == "uncertain"
    # uncertain + user_questions_asked > 0 → confidence ok, but L2/L3 unknown
    # → partial (not pass, not needs_review)
    assert v.overall == "partial"


def test_build_verdict_to_dict_is_json_serializable():
    import json

    v = build_verdict(structural_pass=True, assistant_final_text="done")
    d = v.to_dict()
    # Round-trip through json without exception
    encoded = json.dumps(d)
    decoded = json.loads(encoded)
    assert decoded["L1_structural"] == "pass"
    assert decoded["disclosure"] == "missing"
    assert decoded["overall"] == "needs_review"  # missing disclosure → not pass
