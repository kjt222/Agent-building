"""Re-score a P12.2.3 run using the updated probe (no model re-call).

Usage:
    python rescore_run.py <run_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from run_pptx_dual_model_live import _probe_pptx, _build_report  # type: ignore


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: rescore_run.py <run_dir>")
        return 2
    run_dir = Path(sys.argv[1])
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    for profile, rec in (summary.get("results") or {}).items():
        case_dir = run_dir / profile.replace("/", "_")
        target = case_dir / "output.pptx"
        struct = _probe_pptx(target)
        rec["pptx_structure"] = struct
        rec["task_complete"] = bool(
            struct.get("exists")
            and struct.get("slide_count", 0) >= 3
            and struct.get("has_rectangle")
        )
        (case_dir / "pptx_structure.json").write_text(
            json.dumps(struct, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (case_dir / "summary.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = _build_report(run_dir, summary["results"])
    (run_dir / "comparison_report.md").write_text(report, encoding="utf-8")
    print(json.dumps({
        "rescored": True,
        "task_complete": {p: r.get("task_complete") for p, r in summary["results"].items()},
        "report_md": str(run_dir / "comparison_report.md"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
