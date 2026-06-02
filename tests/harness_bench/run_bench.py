"""Discover task_NN_*.py modules and run them against a fixed model profile.

Each task module declares PROMPT/MODE/setup()/verify(). The runner spins up the
FastAPI server once, runs every task in sequence, and writes a JSON report.

Usage:
    python -m tests.harness_bench.run_bench --profile deepseek-v4
    python -m tests.harness_bench.run_bench --profile deepseek-v4 --tasks 13,14
"""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Any

from agent.config_loader import load_models_config

from .agent_runner import server, run_prompt
from .base import RunOutcome, TaskResult


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "tests" / "harness_bench" / "bench_results"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _discover_tasks(filter_ids: set[str] | None = None) -> list[tuple[str, Any]]:
    """Return list of (task_id, module) sorted by task number.

    Task id is normalized to its int-string form ("01" -> "1") so the --tasks
    filter accepts either "--tasks 1,2,3" or "--tasks 01,02,03".
    """
    import tests.harness_bench as pkg
    norm_filter = {str(int(t)) for t in filter_ids if t.isdigit()} if filter_ids else None
    tasks: list[tuple[str, Any]] = []
    pkg_path = Path(pkg.__file__).parent
    for info in pkgutil.iter_modules([str(pkg_path)]):
        if not info.name.startswith("task_"):
            continue
        # task_13_zero_byte_dataurl -> "13"; task_01_list_embedded_files -> "1"
        parts = info.name.split("_", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        task_id = str(int(parts[1]))
        if norm_filter and task_id not in norm_filter:
            continue
        mod = importlib.import_module(f"tests.harness_bench.{info.name}")
        tasks.append((task_id, mod))
    tasks.sort(key=lambda x: int(x[0]))
    return tasks


def _run_one(base_url: str, task_id: str, mod: Any, profile: str, out_root: Path) -> TaskResult:
    task_out = out_root / f"task_{task_id}"
    task_out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    state: dict[str, Any] = {}
    outcome = RunOutcome()
    profile_override = getattr(mod, "PROFILE_OVERRIDE", "") or profile
    category = "ok"
    passed = False
    reason = ""

    # --- setup ---
    try:
        setup_fn = getattr(mod, "setup", None)
        if setup_fn:
            state = setup_fn() or {}
    except Exception as exc:
        category = "setup_error"
        reason = f"setup_error: {type(exc).__name__}: {exc}"
        (task_out / "setup_error.txt").write_text(traceback.format_exc(), encoding="utf-8")

    # --- run agent (unless setup blew up, or NEEDS_AGENT=False for verifier-sanity) ---
    if category == "ok":
        needs_agent = bool(getattr(mod, "NEEDS_AGENT", True))
        prompt = getattr(mod, "PROMPT", "")
        mode = getattr(mod, "MODE", "read-only")
        timeout_s = float(getattr(mod, "TIMEOUT_S", 240.0))
        # 0 = unlimited (Claude Code mode). Per-task override still respected
        # if a task sets MAX_ITERATIONS, but the default is no cap.
        max_iterations = int(getattr(mod, "MAX_ITERATIONS", 0))
        if isinstance(state, dict) and state.get("_prompt"):
            prompt = state["_prompt"]

        if needs_agent and not prompt:
            category = "setup_error"
            reason = ("setup_error: NEEDS_AGENT=True but PROMPT is empty and "
                      "setup() did not set state['_prompt']")
        elif needs_agent:
            outcome = run_prompt(
                base_url, prompt,
                profile=profile_override, mode=mode,
                title=f"bench task_{task_id}",
                timeout_s=timeout_s,
                max_iterations=max_iterations,
                out_dir=task_out,
            )
            if outcome.error:
                # Server crash / timeout / network — verifier would mis-attribute.
                category = "run_error"
                reason = f"run_error: {outcome.error}"

    # --- verify (skip if run already failed) ---
    if category == "ok":
        try:
            passed, reason = mod.verify(outcome, state)
            reason = str(reason)
            if not isinstance(passed, bool):
                category = "verify_error"
                reason = f"verify_error: returned non-bool {type(passed).__name__}"
                passed = False
        except Exception as exc:
            category = "verify_error"
            reason = f"verify_error: {type(exc).__name__}: {exc}"
            (task_out / "verify_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            passed = False

    # --- teardown (always; surface failures, never swallow silently) ---
    td = getattr(mod, "teardown", None)
    if td:
        try:
            td(state)
        except Exception as exc:
            print(f"[harness_bench] teardown warning task_{task_id}: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)

    result = TaskResult(
        task_id=task_id,
        profile=profile,
        effective_profile=profile_override,
        passed=bool(passed),
        reason=reason,
        elapsed_s=round(time.time() - started, 2),
        category=category,
        outcome=outcome.as_dict() if isinstance(outcome, RunOutcome) else {},
        setup_state={k: v for k, v in state.items() if k != "_prompt"} if isinstance(state, dict) else {},
    )
    (task_out / "result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, help="model profile from config/models.yaml")
    parser.add_argument("--tasks", default="", help="comma-separated task IDs (default: all)")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--base-url", default="", help="reuse an already-running server")
    args = parser.parse_args()

    filter_ids = {t.strip() for t in args.tasks.split(",") if t.strip()} if args.tasks else None
    tasks = _discover_tasks(filter_ids)
    if not tasks:
        print(f"[harness_bench] no tasks discovered (filter={filter_ids})", file=sys.stderr)
        return 2

    # Validate --profile against config/models.yaml (and any PROFILE_OVERRIDE).
    models_cfg = load_models_config()
    known_profiles = set((models_cfg.get("profiles") or {}).keys())
    profiles_used = {args.profile} | {
        getattr(mod, "PROFILE_OVERRIDE", "") for _, mod in tasks
    }
    profiles_used.discard("")
    unknown = profiles_used - known_profiles
    if unknown:
        print(f"[harness_bench] unknown profile(s): {sorted(unknown)}", file=sys.stderr)
        print(f"[harness_bench] known: {sorted(known_profiles)}", file=sys.stderr)
        return 2

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.out_dir) if args.out_dir else DEFAULT_OUT / f"{ts}_{args.profile}"
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"[harness_bench] profile={args.profile}  tasks={[t for t, _ in tasks]}  out={out_root}")

    results: list[TaskResult] = []

    def _run_all(base_url: str) -> None:
        for task_id, mod in tasks:
            print(f"[harness_bench] -> task_{task_id} ({mod.__name__})")
            r = _run_one(base_url, task_id, mod, args.profile, out_root)
            print(f"[harness_bench] <- task_{task_id}  passed={r.passed}  ({r.elapsed_s}s)  {r.reason[:120]}")
            results.append(r)

    # Default NEEDS_AGENT=True so adding a new task without setting it errs on
    # the side of starting the server (the safe failure mode).
    needs_server = any(bool(getattr(mod, "NEEDS_AGENT", True)) for _, mod in tasks)
    if args.base_url:
        _run_all(args.base_url)
    elif needs_server:
        with server(out_root) as base_url:
            _run_all(base_url)
    else:
        _run_all(base_url="")

    # Tier names are P18-prefixed to avoid collision with agent.eval's
    # Tier A (Word/Excel/PPTX office suite). Reviewer flagged this 2026-05-26.
    by_tier_pass: dict[str, list[str]] = {}
    by_tier_fail: dict[str, list[str]] = {}
    for r in results:
        tid = int(r.task_id)
        if 1 <= tid <= 3:
            tier = "P18-A"
        elif 4 <= tid <= 8:
            tier = "P18-B"
        elif 9 <= tid <= 12:
            tier = "P18-C"
        else:
            tier = "P18-D"
        (by_tier_pass if r.passed else by_tier_fail).setdefault(tier, []).append(r.task_id)

    # P18.1.4: roll up silent-handoff flags across all tasks so summary
    # makes it obvious whether failures are root-caused by handoff/script-
    # abandonment patterns vs real verifier disagreements.
    by_flag: dict[str, list[str]] = {}
    for r in results:
        for flag in (r.outcome.get("silent_handoff_flags") or []):
            by_flag.setdefault(flag, []).append(r.task_id)

    summary = {
        "profile": args.profile,
        "timestamp": ts,
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "elapsed_total_s": round(sum(r.elapsed_s for r in results), 2),
        "by_tier_pass": {k: sorted(v, key=int) for k, v in by_tier_pass.items()},
        "by_tier_fail": {k: sorted(v, key=int) for k, v in by_tier_fail.items()},
        "by_silent_handoff_flag": {
            k: sorted(set(v), key=int) for k, v in by_flag.items()
        },
        "tasks": [asdict(r) for r in results],
    }
    (out_root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\n[harness_bench] DONE  {summary['passed']}/{summary['total']} passed  ({summary['elapsed_total_s']}s)")
    print(f"[harness_bench] by_tier_pass={summary['by_tier_pass']}")
    print(f"[harness_bench] by_tier_fail={summary['by_tier_fail']}")
    if summary["by_silent_handoff_flag"]:
        print(f"[harness_bench] by_silent_handoff_flag={summary['by_silent_handoff_flag']}")
    print(f"[harness_bench] summary: {out_root / 'summary.json'}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
