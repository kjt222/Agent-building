"""Case runner — translate ``EvalCase`` to subprocess + score."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from agent.eval.case import CaseResult, EvalCase, ScoreResult


ROOT = Path(__file__).resolve().parents[2]
APP_YAML = ROOT / "config" / "app.yaml"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON_CMD = str(VENV_PY) if VENV_PY.exists() else sys.executable


@dataclass(frozen=True)
class PlannedInvocation:
    cmd: tuple[str, ...]
    artifact_root: Path
    output_path_template: str
    needs_base_url: bool
    timeout_s: float


def plan_invocation(
    case: EvalCase,
    *,
    model: str,
    results_root: Path,
    base_url: str | None = None,
) -> PlannedInvocation:
    if not case.invocation.runner_path.exists():
        raise FileNotFoundError(
            f"runner script not found for case {case.id!r}: "
            f"{case.invocation.runner_path}"
        )
    ts = time.strftime("%Y%m%d_%H%M%S")
    artifact_root = (results_root / case.id / f"{ts}_{model}").resolve()
    rendered_args = []
    for arg in case.invocation.args:
        rendered_args.append(
            arg.format(
                model=model,
                artifact_root=str(artifact_root),
                base_url=base_url or "",
            )
        )
    if case.invocation.needs_base_url and base_url:
        rendered_args.extend(["--base-url", base_url])
    cmd = (PYTHON_CMD, str(case.invocation.runner_path), *rendered_args)
    return PlannedInvocation(
        cmd=cmd,
        artifact_root=artifact_root,
        output_path_template=case.output_path_template,
        needs_base_url=case.invocation.needs_base_url,
        timeout_s=case.invocation.timeout_s,
    )


def resolve_output_path(
    template: str,
    artifact_root: Path,
    *,
    min_mtime: float | None = None,
) -> Path | None:
    """Resolve a template path with ``*`` glob support.

    Supported placeholders: ``{artifact_root}``, ``{repo_root}``,
    ``{tests_root}``. ``*`` works as a path segment wildcard; the
    newest match by mtime is returned. ``None`` when nothing matches.

    ``min_mtime`` (epoch seconds) filters glob results to files modified at
    or after that instant — prevents stale pre-run summaries from being
    scored when a shared runner output dir is reused across cases.
    """
    rendered = template.format(
        artifact_root=str(artifact_root),
        repo_root=str(ROOT),
        tests_root=str(ROOT / "tests"),
    )
    if "*" not in rendered:
        p = Path(rendered)
        if not p.exists():
            return None
        if min_mtime is not None and p.stat().st_mtime < min_mtime:
            return None
        return p
    # Find the longest prefix without a wildcard, then glob the rest.
    p = Path(rendered)
    parts = list(p.parts)
    split_idx = next((i for i, seg in enumerate(parts) if "*" in seg), len(parts))
    base = Path(*parts[:split_idx]) if split_idx else Path(parts[0])
    if not base.exists():
        return None
    rel_pattern = str(Path(*parts[split_idx:])) if split_idx < len(parts) else ""
    candidates = list(base.glob(rel_pattern))
    if min_mtime is not None:
        candidates = [
            c for c in candidates
            if c.exists() and c.stat().st_mtime >= min_mtime
        ]
    matches = sorted(
        candidates,
        key=lambda x: x.stat().st_mtime if x.exists() else 0,
        reverse=True,
    )
    return matches[0] if matches else None


def score_output(case: EvalCase, output_path: Path, *, model_label: str = "") -> ScoreResult:
    scorer = case.scorer_factory()
    if model_label and hasattr(scorer, "_model_label"):
        if scorer._model_label == "{model_label}":
            object.__setattr__(scorer, "_model_label", model_label)
    return scorer.score(output_path)


def make_dry_run_result(case: EvalCase, model: str, plan: PlannedInvocation) -> CaseResult:
    return CaseResult(
        case_id=case.id,
        model=model,
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        elapsed_s=0.0,
        output_path=None,
        invocation_cmd=plan.cmd,
        score=ScoreResult(
            passed=False,
            details={"dry_run": True, "would_run": True},
            error="dry-run (no subprocess started)",
        ),
    )


def execute_case(
    case: EvalCase,
    *,
    model: str,
    results_root: Path,
    base_url: str | None = None,
    capture_tail_chars: int = 600,
) -> CaseResult:
    """Spawn the runner subprocess, wait, score the output."""
    plan = plan_invocation(case, model=model, results_root=results_root, base_url=base_url)
    plan.artifact_root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    started_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
    stdout_text = ""
    stderr_text = ""
    timed_out = False
    try:
        proc = subprocess.run(
            list(plan.cmd),
            cwd=str(ROOT),
            timeout=plan.timeout_s,
            capture_output=True,
            text=True,
            env=dict(os.environ),
        )
        stdout_text = proc.stdout or ""
        stderr_text = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout_text = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, (bytes, bytearray)) else (exc.stdout or "")
        stderr_text = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, (bytes, bytearray)) else (exc.stderr or "")
    except Exception as exc:
        return CaseResult(
            case_id=case.id,
            model=model,
            started_at=started_iso,
            elapsed_s=round(time.time() - started, 2),
            output_path=None,
            invocation_cmd=plan.cmd,
            score=ScoreResult(passed=False, error=f"subprocess failed: {exc}"),
            stdout_tail="",
            stderr_tail=str(exc)[:capture_tail_chars],
        )

    elapsed = round(time.time() - started, 2)
    # min_mtime = started - 1s tolerance for clock-skew / sub-second timing
    # gaps between this process's `started` and the runner subprocess's first
    # file write. Without this guard, glob templates like
    # `tests/results/p11_powerpoint_layout_verifier/*/summary.json` pick the
    # newest *historical* file when the current runner failed to produce
    # output, falsely scoring against a prior case's results.
    output_path = resolve_output_path(
        plan.output_path_template,
        plan.artifact_root,
        min_mtime=started - 1.0,
    )
    if timed_out:
        score = ScoreResult(
            passed=False,
            error=f"timeout after {plan.timeout_s}s",
            details={"timed_out": True},
        )
    elif output_path is None:
        score = ScoreResult(
            passed=False,
            error="runner did not produce expected output file",
            details={"expected": plan.output_path_template},
        )
    else:
        score = score_output(case, output_path, model_label=model)

    return CaseResult(
        case_id=case.id,
        model=model,
        started_at=started_iso,
        elapsed_s=elapsed,
        output_path=output_path,
        invocation_cmd=plan.cmd,
        score=score,
        stdout_tail=stdout_text[-capture_tail_chars:] if stdout_text else "",
        stderr_tail=stderr_text[-capture_tail_chars:] if stderr_text else "",
    )


@contextmanager
def temporary_active_profile(target: str) -> Iterator[str]:
    """Swap ``active_profile`` in config/app.yaml for the duration of the block."""
    text = APP_YAML.read_text(encoding="utf-8")
    lines = text.splitlines()
    old = ""
    for i, line in enumerate(lines):
        if line.startswith("active_profile:"):
            old = line.split(":", 1)[1].strip()
            lines[i] = f"active_profile: {target}"
            break
    APP_YAML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        yield old
    finally:
        if old:
            text2 = APP_YAML.read_text(encoding="utf-8")
            lines2 = text2.splitlines()
            for i, line in enumerate(lines2):
                if line.startswith("active_profile:"):
                    lines2[i] = f"active_profile: {old}"
                    break
            APP_YAML.write_text("\n".join(lines2) + "\n", encoding="utf-8")


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    return {
        "cases": [
            {
                "case_id": r.case_id,
                "model": r.model,
                "started_at": r.started_at,
                "elapsed_s": r.elapsed_s,
                "passed": r.score.passed,
                "score": r.score.score,
                "error": r.score.error,
                "details": r.score.details,
                "invocation_cmd": list(r.invocation_cmd),
                "output_path": str(r.output_path) if r.output_path else None,
                "stdout_tail": r.stdout_tail,
                "stderr_tail": r.stderr_tail,
            }
            for r in results
        ],
        "totals": {
            "n": len(results),
            "passed": sum(1 for r in results if r.score.passed),
            "failed": sum(1 for r in results if not r.score.passed),
        },
    }
