"""Reporter - 汇总评估报告

将多条 GradeResult 汇总为结构化报告，支持：
- 按 category 分组统计
- 通过率 / 平均分
- JSON 输出到 results/
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .trace_grader import GradeResult


RESULTS_DIR = Path(__file__).parent.parent / "results"


def summarize(results: list[GradeResult]) -> dict:
    """生成汇总统计"""
    if not results:
        return {"total": 0, "passed": 0, "pass_rate": 0.0, "avg_score": 0.0}

    by_category: dict[str, list[GradeResult]] = defaultdict(list)
    for r in results:
        cat = r.details.get("category", "unknown")
        by_category[cat].append(r)

    total = len(results)
    passed = sum(1 for r in results if r.passed)

    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(passed / total, 3),
        "avg_score": round(sum(r.score for r in results) / total, 3),
        "by_category": {
            cat: {
                "total": len(items),
                "passed": sum(1 for r in items if r.passed),
                "avg_score": round(sum(r.score for r in items) / len(items), 3),
            }
            for cat, items in by_category.items()
        },
    }


def write_report(results: list[GradeResult], tag: str = "") -> Path:
    """将详细结果和汇总写入 JSON 文件"""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    filename = f"eval_{tag}_{timestamp}.json" if tag else f"eval_{timestamp}.json"

    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": summarize(results),
        "details": [asdict(r) for r in results],
    }

    out_path = RESULTS_DIR / filename
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
