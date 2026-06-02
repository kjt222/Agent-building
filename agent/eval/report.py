"""Markdown comparison report writer."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Iterable

from agent.eval.baseline import BaselineDiff
from agent.eval.case import CaseResult


def write_comparison_md(
    out_path: Path,
    *,
    suite: str,
    results: Iterable[CaseResult],
    diffs: dict[str, BaselineDiff] | None = None,
) -> Path:
    """Write a cross-model summary table + per-model diff section."""
    by_case: dict[str, dict[str, CaseResult]] = defaultdict(dict)
    models: list[str] = []
    for r in results:
        if r.model not in models:
            models.append(r.model)
        by_case[r.case_id][r.model] = r

    lines: list[str] = []
    lines.append(f"# Eval report — suite `{suite}`\n")
    lines.append("Generated automatically by `agent.eval`.\n")

    lines.append("## Pass matrix\n")
    header = "| Case | " + " | ".join(models) + " |"
    sep = "|" + "---|" * (len(models) + 1)
    lines.append(header)
    lines.append(sep)
    for case_id in sorted(by_case):
        row = [case_id]
        for model in models:
            r = by_case[case_id].get(model)
            if r is None:
                row.append("—")
            elif r.score.passed:
                row.append(f"✅ {r.elapsed_s:.1f}s")
            else:
                err_excerpt = (r.score.error or "fail")[:40]
                row.append(f"❌ {err_excerpt}")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Per-model totals\n")
    lines.append("| Model | Passed | Failed | Total |")
    lines.append("|---|---|---|---|")
    for model in models:
        rs = [by_case[c][model] for c in by_case if model in by_case[c]]
        p = sum(1 for r in rs if r.score.passed)
        f = sum(1 for r in rs if not r.score.passed)
        lines.append(f"| {model} | {p} | {f} | {len(rs)} |")
    lines.append("")

    if diffs:
        lines.append("## Baseline diff\n")
        for model, diff in diffs.items():
            lines.append(f"### {model}\n")
            for label, ids in [
                ("✅ New passes (improvement)", diff.new_pass),
                ("❌ New failures (regression)", diff.new_fail),
                ("➕ Cases new to baseline", diff.new_case),
                ("⚠️ Cases dropped from current run", diff.missing),
            ]:
                if ids:
                    lines.append(f"- **{label}**: {', '.join(ids)}")
            lines.append("")

    lines.append("## Per-case details\n")
    for case_id in sorted(by_case):
        lines.append(f"### `{case_id}`\n")
        for model in models:
            r = by_case[case_id].get(model)
            if r is None:
                continue
            outcome = "PASS" if r.score.passed else "FAIL"
            lines.append(f"- **{model}** — {outcome} in {r.elapsed_s:.1f}s")
            if r.score.error:
                lines.append(f"  - error: `{r.score.error[:200]}`")
            failing = (r.score.details or {}).get("failing_criteria")
            if failing:
                lines.append(f"  - failing criteria: `{failing}`")
            cmd_str = " ".join(r.invocation_cmd)
            if cmd_str:
                lines.append(f"  - cmd: `{cmd_str}`")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
