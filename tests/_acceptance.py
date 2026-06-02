"""Shared acceptance verdict utilities for smoke runners (P14.1.3).

Three-tier framework defined in docs/conversation.md P14:

  L1 structural    — file parses, fields present (runner's existing checks)
  L2 semantic      — domain oracle says output is meaningful (P14.2, opt-in)
  L3 user view     — vision judge says it looks right (P14.3, opt-in)

Plus two non-tier axes:
  model_self_confidence — pass / uncertain / fail (model's own self-rating)
  disclosure            — present / missing (did model list Unverified items?)
  user_questions_asked  — count of AskUserQuestion calls

`overall` is computed so a green light requires the WHOLE chain — L1=pass AND
L2=pass AND L3=pass AND disclosure=present AND
(model_self_confidence == "pass" OR user_questions_asked > 0). Anything weaker
downgrades to `needs_review`. This stops the recurring failure mode where L1
passes are reported as "闭环" while user-visible problems remain.

Runners populate `L1_structural` from their existing structural verdict, and
let the rest default to `unknown` / `missing` until the corresponding tier
lands. As tiers come online they pass results in via the keyword args.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

Verdict = Literal["pass", "fail", "warn", "unknown"]
Disclosure = Literal["present", "missing", "unknown"]
SelfConfidence = Literal["pass", "uncertain", "fail", "unknown"]
Overall = Literal["pass", "needs_review", "partial", "fail"]


# Mirrors agent/core/hooks.py _DISCLOSURE_PRESENT_PATTERN so runner reads the
# model's *output* the same way the disclosure_guard nudge does. Keep these in
# sync — if the model writes something the hook accepts, the runner should
# also accept it, and vice versa.
_DISCLOSURE_PATTERN = re.compile(
    r"unverified\s+items?|please\s+verify|can(?:not|'t|\s+not)\s+verify|"
    r"i\s+(?:cannot|can't|can\s+not)\s+(?:open|render|check|view)|"
    r"need(?:s)?\s+(?:your|user)\s+(?:visual|manual)\s+check|"
    r"未验证项|未验证|我无法验证|请你检查|"
    r"请检查|需要你(?:手动|视觉)确认|需手动确认|"
    r"我不能打开",
    re.IGNORECASE,
)


def detect_disclosure(assistant_final_text: str) -> Disclosure:
    """Did the model's final message include a `Unverified items` block?"""
    if not assistant_final_text:
        return "unknown"
    return "present" if _DISCLOSURE_PATTERN.search(assistant_final_text) else "missing"


# Self-confidence tag emitted by the model per the base_agent_prompt's
# `<acceptance_policy>`. Last occurrence wins (if model emits multiple
# during streaming or self-correction). Tolerant to surrounding whitespace,
# but a tag wrapped in code fences still matches — the regex doesn't care
# about the fence, it just finds the literal tag substring.
_SELF_CONFIDENCE_PATTERN = re.compile(
    r"<self_confidence>\s*(pass|uncertain|fail)\s*</self_confidence>",
    re.IGNORECASE,
)


def parse_self_confidence(assistant_final_text: str) -> SelfConfidence:
    """Extract the model's `<self_confidence>` tag. Unknown if missing.

    Behavior:
      - Last matching tag wins (handles self-correction in streaming output).
      - Case insensitive on the verdict value (PASS / Pass / pass all work).
      - Tag-inside-code-fence still matches — we just scan for the substring.
      - Empty string or whitespace-only → "unknown".
    """
    if not assistant_final_text or not assistant_final_text.strip():
        return "unknown"
    matches = _SELF_CONFIDENCE_PATTERN.findall(assistant_final_text)
    if not matches:
        return "unknown"
    return matches[-1].lower()  # type: ignore[return-value]


def count_ask_user_questions(tool_calls: Iterable[dict]) -> int:
    """Count AskUserQuestion invocations in the call log."""
    return sum(
        1
        for c in tool_calls
        if (c.get("name") or "").lower().replace("_", "").replace(" ", "")
        == "askuserquestion"
    )


@dataclass
class AcceptanceVerdict:
    L1_structural: Verdict = "unknown"
    L2_semantic: Verdict = "unknown"
    L3_user_view: Verdict = "unknown"
    model_self_confidence: SelfConfidence = "unknown"
    user_questions_asked: int = 0
    disclosure: Disclosure = "unknown"
    overall: Overall = field(init=False, default="needs_review")
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.overall = self._compute_overall()

    def _compute_overall(self) -> Overall:
        # Any explicit fail short-circuits.
        if "fail" in (self.L1_structural, self.L2_semantic, self.L3_user_view):
            return "fail"
        if self.model_self_confidence == "fail":
            return "fail"

        # Strict pass — every gate green.
        tiers_all_pass = (
            self.L1_structural == "pass"
            and self.L2_semantic == "pass"
            and self.L3_user_view == "pass"
        )
        confidence_ok = (
            self.model_self_confidence == "pass" or self.user_questions_asked > 0
        )
        if tiers_all_pass and self.disclosure == "present" and confidence_ok:
            return "pass"

        # Partial: at least L1 passed, but some downstream tier hasn't run yet
        # (unknown) and no failures. Differentiate from needs_review so
        # runner reports can tell "we haven't checked further" from "we
        # checked further and it's not OK".
        anyone_failed_or_warned = any(
            v in ("fail", "warn")
            for v in (self.L1_structural, self.L2_semantic, self.L3_user_view)
        )
        if (
            self.L1_structural == "pass"
            and not anyone_failed_or_warned
            and self.disclosure == "present"
            and confidence_ok
        ):
            return "partial"

        return "needs_review"

    def to_dict(self) -> dict[str, Any]:
        return {
            "L1_structural": self.L1_structural,
            "L2_semantic": self.L2_semantic,
            "L3_user_view": self.L3_user_view,
            "model_self_confidence": self.model_self_confidence,
            "user_questions_asked": self.user_questions_asked,
            "disclosure": self.disclosure,
            "overall": self.overall,
            "notes": list(self.notes),
        }


def _coerce_tier(value: bool | str | None) -> Verdict:
    """Accept bool / verdict string / None for each tier.

    `True` → "pass", `False` → "fail", `None` → "unknown". String inputs are
    passed through if they're a known Verdict literal, else "unknown". This
    lets runners pass an oracle's `OracleReport.verdict` (which can be
    "warn") directly without translation.
    """
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if isinstance(value, str) and value in ("pass", "fail", "warn", "unknown"):
        return value  # type: ignore[return-value]
    return "unknown"


def build_verdict(
    *,
    structural_pass: bool | str | None = None,
    semantic_pass: bool | str | None = None,
    user_view_pass: bool | str | None = None,
    model_self_confidence: SelfConfidence = "unknown",
    assistant_final_text: str = "",
    tool_calls: Iterable[dict] = (),
    notes: Iterable[str] = (),
) -> AcceptanceVerdict:
    """Convenience constructor — runners pass what they know, defaults fill in.

    Each `*_pass` accepts bool / verdict string / None. See `_coerce_tier`.
    """
    # If caller didn't override, try to parse the tag from the model's text.
    if model_self_confidence == "unknown":
        model_self_confidence = parse_self_confidence(assistant_final_text)

    return AcceptanceVerdict(
        L1_structural=_coerce_tier(structural_pass),
        L2_semantic=_coerce_tier(semantic_pass),
        L3_user_view=_coerce_tier(user_view_pass),
        model_self_confidence=model_self_confidence,
        user_questions_asked=count_ask_user_questions(tool_calls),
        disclosure=detect_disclosure(assistant_final_text),
        notes=list(notes),
    )
