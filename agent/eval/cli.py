"""``python -m agent.eval`` entry point."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent.eval.baseline import (
    baseline_path,
    diff_against,
    dump_baseline,
    load_baseline,
)
from agent.eval.case import CaseResult
from agent.eval.registry import build_registry
from agent.eval.report import write_comparison_md
from agent.eval.runner import (
    execute_case,
    make_dry_run_result,
    plan_invocation,
    summarise,
    temporary_active_profile,
)


def _cmd_list(args: argparse.Namespace) -> int:
    cases = build_registry(args.suite)
    if args.format == "json":
        out = [
            {
                "id": c.id,
                "suite": c.suite,
                "title": c.title,
                "tags": list(c.tags),
                "suggested_models": list(c.suggested_models),
            }
            for c in cases
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"suite={args.suite}, {len(cases)} case(s):")
    for c in cases:
        models = ", ".join(c.suggested_models) or "—"
        print(f"  {c.id:<48} {c.title}")
        print(f"    tags: {', '.join(c.tags) or '—'}    models: {models}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    cases = build_registry(args.suite)
    if args.filter:
        cases = [c for c in cases if args.filter in c.id]
    if not cases:
        print(f"no cases match filter={args.filter!r}", file=sys.stderr)
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        print("--models is required", file=sys.stderr)
        return 1

    results_root = Path(args.results_root).resolve()
    results_root.mkdir(parents=True, exist_ok=True)
    case_results: list[CaseResult] = []
    errors: list[str] = []

    def _run_phase(model_label: str) -> None:
        for case in cases:
            try:
                plan = plan_invocation(
                    case, model=model_label, results_root=results_root,
                    base_url=args.base_url or None,
                )
            except Exception as exc:
                errors.append(f"{case.id}@{model_label}: plan failed: {exc}")
                continue
            if args.dry_run:
                case_results.append(make_dry_run_result(case, model_label, plan))
            else:
                print(f"[run] {case.id} @ {model_label} …", flush=True)
                result = execute_case(
                    case, model=model_label, results_root=results_root,
                    base_url=args.base_url or None,
                )
                outcome = "PASS" if result.score.passed else "FAIL"
                err = f" ({result.score.error[:80]})" if result.score.error else ""
                print(f"  → {outcome} in {result.elapsed_s:.1f}s{err}", flush=True)
                case_results.append(result)

    if args.profile_swap and not args.dry_run:
        # One swap covers the whole sweep — every case run during the
        # ``with`` block reads active_profile = args.profile_swap.
        with temporary_active_profile(args.profile_swap) as old:
            print(f"[profile] swapped active_profile {old!r} -> "
                  f"{args.profile_swap!r}", flush=True)
            for model in models:
                _run_phase(model)
    else:
        for model in models:
            _run_phase(model)

    summary = summarise(case_results)
    summary["errors"] = errors
    summary["dry_run"] = bool(args.dry_run)
    summary["profile_swap"] = args.profile_swap

    out_path = results_root / "summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    # Baseline handling
    baselines_root = Path(args.baselines_root).resolve()
    diffs = {}
    if args.baseline_check and not args.dry_run:
        for model in models:
            bp = baseline_path(baselines_root, args.suite, model)
            baseline = load_baseline(bp)
            results_for_model = [r for r in case_results if r.model == model]
            diff = diff_against(results_for_model, baseline)
            diffs[model] = diff
            print(
                f"[baseline] {model}: "
                f"+pass={len(diff.new_pass)} +fail={len(diff.new_fail)} "
                f"unchanged_pass={len(diff.unchanged_pass)} "
                f"unchanged_fail={len(diff.unchanged_fail)} "
                f"missing={len(diff.missing)} new={len(diff.new_case)}",
                flush=True,
            )

    if args.baseline_update and not args.dry_run:
        for model in models:
            bp = baseline_path(baselines_root, args.suite, model)
            results_for_model = [r for r in case_results if r.model == model]
            dump_baseline(
                bp, suite=args.suite, model=model, results=results_for_model,
                note=f"updated via agent.eval run --baseline-update",
            )
            print(f"[baseline] wrote {bp}", flush=True)

    # Markdown report
    if not args.dry_run:
        report_path = write_comparison_md(
            results_root / "comparison_report.md",
            suite=args.suite, results=case_results,
            diffs=diffs or None,
        )
        print(f"[report] {report_path}", flush=True)

    print(json.dumps({
        "results_root": str(results_root),
        "summary_path": str(out_path),
        "cases_run": summary["totals"]["n"],
        "passed": summary["totals"].get("passed"),
        "failed": summary["totals"].get("failed"),
        "errors": errors,
    }, ensure_ascii=False, indent=2))

    # Exit code: dry-run = 0 (planning succeeded); live = 0 if all pass.
    if args.dry_run:
        return 0 if not errors and case_results else 1
    if args.baseline_check and any(d.has_regression for d in diffs.values()):
        return 2  # regression
    return 0 if summary["totals"].get("failed", 0) == 0 and not errors else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent.eval")
    sub = parser.add_subparsers(dest="cmd")
    sub.required = True

    list_p = sub.add_parser("list", help="List cases in a suite.")
    list_p.add_argument("--suite", default="tier_a")
    list_p.add_argument("--format", choices=("text", "json"), default="text")
    list_p.set_defaults(func=_cmd_list)

    run_p = sub.add_parser("run", help="Plan or execute case runs.")
    run_p.add_argument("--suite", default="tier_a")
    run_p.add_argument("--models", required=True,
                       help="Comma-separated model labels (e.g. doubao-code,gpt-5.5).")
    run_p.add_argument("--filter", default="",
                       help="Substring filter on case id.")
    run_p.add_argument("--base-url", default="",
                       help="External uvicorn URL for runners that need one.")
    run_p.add_argument("--results-root", default="tests/results/eval/latest")
    run_p.add_argument("--baselines-root", default="tests/eval_baselines")
    run_p.add_argument("--dry-run", action="store_true",
                       help="Plan commands without starting subprocesses.")
    run_p.add_argument("--profile-swap", default="",
                       help="Swap config/app.yaml active_profile for the sweep.")
    run_p.add_argument("--baseline-check", action="store_true",
                       help="Compare current run against checked-in baseline.")
    run_p.add_argument("--baseline-update", action="store_true",
                       help="Write current run as the new baseline.")
    run_p.set_defaults(func=_cmd_run)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
