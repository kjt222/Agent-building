"""Case registry.

Tier A — multi-scenario eval-style runners (P12.2.3 inventory).
Tier B — single-prompt smokes that produce summary.json with passed/cases.

Slice 1 hand-maintained; slice 4 may extract a YAML manifest, but
explicit registration keeps the "what's wired" answer scannable.
"""

from __future__ import annotations

from pathlib import Path

from agent.eval.case import EvalCase, Invocation
from agent.eval.scorer import (
    ChecksDictScorer,
    ListSummaryScorer,
    PptxLayoutScorer,
    Scorer,
)

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"


def _word_short_scorer(scenario: str, model_label: str) -> Scorer:
    return ListSummaryScorer(
        scenario=scenario,
        model_label=model_label,
        criteria=[
            ("timed_out", lambda v: v is False),
            ("checks.table_count", lambda v: isinstance(v, int) and v >= 1),
            ("checks.heading_styles", lambda v: isinstance(v, dict) and len(v) >= 4),
        ],
    )


def _excel_short_scorer(scenario: str, model_label: str) -> Scorer:
    return ListSummaryScorer(
        scenario=scenario,
        model_label=model_label,
        criteria=[
            ("timed_out", lambda v: v is False),
            ("checks.workbook_opens", lambda v: v is True),
            ("checks.cells_written_count", lambda v: isinstance(v, int) and v >= 1),
        ],
    )


def _skills_smoke_scorer() -> Scorer:
    """All sub-cases in p9_skills_live_smoke must pass."""

    class _SkillsScorer:
        def score(self, output_path, *, verifier=None):
            from agent.eval.case import ScoreResult
            import json
            if not output_path.exists():
                return ScoreResult(passed=False, error=f"missing {output_path}")
            data = json.loads(output_path.read_text(encoding="utf-8"))
            cases = data.get("cases") or []
            if not cases:
                return ScoreResult(passed=False, error="no sub-cases reported")
            failing = [c.get("id") for c in cases if not c.get("passed")]
            return ScoreResult(
                passed=not failing and bool(data.get("passed")),
                details={
                    "sub_case_count": len(cases),
                    "sub_case_passed": sum(1 for c in cases if c.get("passed")),
                    "failing_ids": failing,
                },
            )

    return _SkillsScorer()


def build_tier_a() -> list[EvalCase]:
    cases: list[EvalCase] = []

    word_runner = TESTS / "p4_word_complex_validation" / "run_word_short_eval.py"
    for scenario, title in [
        ("thesis_all_in_one", "Word — thesis once-over cleanup"),
        ("thesis_review_fix", "Word — thesis review-comment fix"),
    ]:
        cases.append(EvalCase(
            id=f"p4_word_{scenario}",
            suite="tier_a",
            title=title,
            prompt_summary="Restructure a thesis-style .docx and inspect headings/TOC/footnotes.",
            invocation=Invocation(
                runner_path=word_runner,
                args=(
                    "--scenario", scenario,
                    "--model", "{model}",
                    "--artifact-root", "{artifact_root}",
                ),
                timeout_s=300.0,
            ),
            output_path_template="{artifact_root}/summary.json",
            scorer_factory=lambda s=scenario: _word_short_scorer(s, "{model_label}"),
            suggested_models=("doubao-code", "gpt-5.5"),
            tags=("office_word", "structural_check", "needs_uvicorn"),
        ))

    excel_runner = TESTS / "p4_excel_complex_validation" / "run_excel_short_eval.py"
    if excel_runner.exists():
        cases.append(EvalCase(
            id="p4_excel_short",
            suite="tier_a",
            title="Excel — multi-sheet short eval",
            prompt_summary="Apply named-range / formula edits to a fixture .xlsx; inspect post state.",
            invocation=Invocation(
                runner_path=excel_runner,
                args=(
                    "--scenario", "all",
                    "--model", "{model}",
                    "--artifact-root", "{artifact_root}",
                ),
                timeout_s=300.0,
            ),
            output_path_template="{artifact_root}/summary.json",
            scorer_factory=lambda: _excel_short_scorer("default", "{model_label}"),
            suggested_models=("doubao-code", "gpt-5.5"),
            tags=("office_excel", "structural_check", "needs_uvicorn"),
        ))

    # p11_powerpoint_layout_verifier runs the full deterministic suite
    # in one shot (no per-task CLI flag); we register two scored "views"
    # over the same shared bundle output. The runner is launched once;
    # subsequent scored rows just re-read the same summary.json that the
    # first run produced (glob finds the latest <ts>/summary.json).
    pptx_verifier_runner = (
        TESTS / "p11_powerpoint_layout_verifier" / "run_layout_verifier_tasks.py"
    )
    if pptx_verifier_runner.exists():
        for mode, task in [
            ("deterministic", "ald_good_with_warning"),
            ("deterministic", "flow_overlap_bad"),
        ]:
            cases.append(EvalCase(
                id=f"p11_pptx_verifier_{mode}_{task}",
                suite="tier_a",
                title=f"PPTX layout verifier — {mode}/{task}",
                prompt_summary="Run deterministic layout verifier; read one row from its bundle summary.",
                invocation=Invocation(
                    runner_path=pptx_verifier_runner,
                    args=(),
                    needs_base_url=False,
                    timeout_s=120.0,
                ),
                output_path_template=(
                    "{tests_root}/results/p11_powerpoint_layout_verifier/*/summary.json"
                ),
                scorer_factory=lambda m=mode, t=task: PptxLayoutScorer(mode=m, task=t),
                suggested_models=("n/a",),
                tags=("office_pptx", "deterministic", "shared_bundle"),
                notes=(
                    "Runner takes no per-row args — it always emits the full "
                    "deterministic rows list. The eval scorer picks one row."
                ),
            ))

    return cases


def build_tier_b() -> list[EvalCase]:
    """Tier B: single-prompt smokes that emit summary.json with .passed.

    Slice 2 minimum — add only runners that:
      - Manage their own uvicorn (no external lifecycle from us)
      - Accept --out-dir or --out so we can sandbox artifacts
      - Already emit summary.json with a top-level ``passed`` bool or
        a ``cases`` list of sub-cases

    More can be added in slice 4 once each adapter is verified.
    """
    cases: list[EvalCase] = []

    skills_runner = TESTS / "p9_skills_live_smoke" / "run_skills_live_smoke.py"
    if skills_runner.exists():
        cases.append(EvalCase(
            id="p9_skills_routing",
            suite="tier_b",
            title="Skills router smoke (zero-token; abort at tool_manifest)",
            prompt_summary=(
                "Drive 10 routing prompts through the agent loop and abort "
                "each request after the tool_manifest activity. Costs zero "
                "model tokens but exercises skill matching + tool exposure."
            ),
            invocation=Invocation(
                runner_path=skills_runner,
                args=("--out-dir", "{artifact_root}"),
                needs_base_url=False,  # runner spawns its own uvicorn
                timeout_s=240.0,
            ),
            output_path_template="{artifact_root}/*/summary.json",
            scorer_factory=_skills_smoke_scorer,
            suggested_models=("doubao-code", "gpt-5.5"),
            tags=("skills", "zero_token", "uvicorn_self_managed"),
            notes=(
                "Runner aborts at tool_manifest so no completion tokens are "
                "consumed. Ideal for cheap doubao behavior sweeps."
            ),
        ))

    return cases


def build_registry(suite: str = "tier_a") -> list[EvalCase]:
    if suite == "tier_a":
        return build_tier_a()
    if suite == "tier_b":
        return build_tier_b()
    if suite == "all":
        return build_tier_a() + build_tier_b()
    raise ValueError(f"unknown suite: {suite!r}")


def get_case(case_id: str, *, suite: str = "all") -> EvalCase:
    for case in build_registry(suite):
        if case.id == case_id:
            return case
    raise KeyError(case_id)
