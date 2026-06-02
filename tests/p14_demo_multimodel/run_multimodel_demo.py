"""P14 full-stack demo: run the same Excalidraw task across multiple models.

For each profile in MODELS, invoke the existing P13 explain_formula_smoke
runner with `--profile <name>`. Collect `summary.json` from each run.
Build a horizontal comparison table on the 5 acceptance axes:

    L1_structural   L2_semantic   L3_user_view   disclosure   self_conf   OVERALL

This is the "is the full P14 acceptance stack actually working end-to-end"
demo. It also serves as the first comparative-quality signal across models.

Cost note: each agent run is ~5-15 min wall-clock and a few hundred K
tokens. L3 vision_judge adds ~$0.05-0.15 per run. 4 models × 1 run each =
roughly $5-15 total at current pricing — within the user's 2026-05-20
multi-model demo budget allowance.

Run:
    .venv/Scripts/python.exe tests/p14_demo_multimodel/run_multimodel_demo.py

To skip a profile (e.g. to debug locally):
    .venv/Scripts/python.exe tests/p14_demo_multimodel/run_multimodel_demo.py --only doubao-code
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "tests" / "p13_obsidian_live_smoke" / "run_explain_formula_smoke.py"
RESULTS_ROOT = ROOT / "tests" / "results" / "p14_demo_multimodel"

# Default model lineup. User's 2026-05-20 request: "DEEPSEEK V4 / GLM 5.1
# 都行" — Volcano Ark may proxy newer variants but we run real provider
# endpoints here to keep the comparison honest.
MODELS = [
    "gpt-5.5",            # OpenAI direct
    "doubao-code",        # Volcano Ark, doubao-seed-2.0-code
    "deepseek-reasoner",  # DeepSeek direct
    "glm-4.7",            # Zhipu direct
]


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _run_one(profile: str, out_dir: Path) -> dict:
    log = out_dir / f"{profile}.stdout.log"
    print(f"\n=========================================================")
    print(f"  PROFILE: {profile}")
    print(f"  log:     {log}")
    print(f"=========================================================\n")
    start = time.time()
    with log.open("w", encoding="utf-8", errors="replace") as f:
        proc = subprocess.run(
            [sys.executable, str(SMOKE), "--profile", profile],
            stdout=f, stderr=subprocess.STDOUT, cwd=str(ROOT),
        )
    elapsed = time.time() - start
    # Smoke writes its own run dir under tests/results/p13_obsidian_live_smoke.
    # Find the freshest one and pull summary.json.
    p13_results = ROOT / "tests" / "results" / "p13_obsidian_live_smoke"
    latest = max(
        (d for d in p13_results.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime, default=None,
    )
    summary: dict = {}
    if latest is not None:
        sj = latest / "summary.json"
        if sj.exists():
            try:
                summary = json.loads(sj.read_text(encoding="utf-8"))
            except Exception as exc:
                summary = {"_parse_error": str(exc)}
    return {
        "profile": profile,
        "exit_code": proc.returncode,
        "elapsed_s": round(elapsed, 1),
        "result_dir": str(latest) if latest else None,
        "summary": summary,
    }


def _render_table(runs: list[dict]) -> str:
    rows = [
        ["PROFILE", "EXIT", "WALL(s)", "L1", "L2", "L3", "DISC", "SELF", "ASKQ", "OVERALL"],
        ["-" * 18, "-" * 4, "-" * 8, "-" * 6, "-" * 6, "-" * 6, "-" * 8, "-" * 9, "-" * 5, "-" * 14],
    ]
    for r in runs:
        v = (r.get("summary") or {}).get("verdict") or {}
        rows.append([
            r["profile"],
            str(r.get("exit_code")),
            str(r.get("elapsed_s")),
            v.get("L1_structural", "?"),
            v.get("L2_semantic", "?"),
            v.get("L3_user_view", "?"),
            v.get("disclosure", "?"),
            v.get("model_self_confidence", "?"),
            str(v.get("user_questions_asked", "?")),
            v.get("overall", "?"),
        ])
    widths = [max(len(str(r[c])) for r in rows) for c in range(len(rows[0]))]
    lines = []
    for r in rows:
        lines.append("  ".join(str(r[c]).ljust(widths[c]) for c in range(len(r))))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", action="append", default=[],
                        help="Restrict to specific profile(s); repeatable.")
    args = parser.parse_args()

    profiles = args.only or MODELS
    out_dir = RESULTS_ROOT / _ts()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[p14-demo] output: {out_dir}")
    print(f"[p14-demo] profiles: {profiles}")

    runs: list[dict] = []
    for profile in profiles:
        try:
            res = _run_one(profile, out_dir)
        except Exception as exc:
            res = {"profile": profile, "exit_code": -1, "exception": str(exc)}
        runs.append(res)
        (out_dir / "runs.json").write_text(
            json.dumps(runs, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n\n=========================================================")
    print("  P14 multi-model demo: results")
    print("=========================================================\n")
    table = _render_table(runs)
    print(table)
    (out_dir / "table.txt").write_text(table, encoding="utf-8")
    print(f"\n[done] {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
