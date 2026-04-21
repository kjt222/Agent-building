"""Agent 评估 CLI 入口

用法:
    python -m tests.run_eval                        # 跑全部任务
    python -m tests.run_eval --category kb_retrieval  # 只跑指定类别
    python -m tests.run_eval --profile 22            # 指定 profile
    python -m tests.run_eval --verbose               # 详细输出
    python -m tests.run_eval --list                  # 列出所有任务（不执行）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.agent_eval.runner import EvalRunner, load_tasks
from tests.agent_eval.trace_grader import TraceGrader
from tests.agent_eval import reporter


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def list_tasks() -> None:
    """列出所有评估任务"""
    tasks_dir = Path(__file__).parent / "fixtures" / "agent_tasks"
    tasks = load_tasks(tasks_dir)
    print(f"\n共 {len(tasks)} 个评估任务:\n")
    by_cat: dict[str, list] = {}
    for t in tasks:
        by_cat.setdefault(t.category, []).append(t)
    for cat, items in sorted(by_cat.items()):
        print(f"  [{cat}] ({len(items)} 个)")
        for t in items:
            must = t.expected_trace.get("must_call", [])
            print(f"    {t.id:20s}  must_call={must}")
    print()


async def run_eval(
    profile_id: int | None = None,
    category: str | None = None,
    verbose: bool = False,
    tag: str = "",
) -> int:
    """执行评估流程，返回退出码"""
    setup_logging(verbose)
    logger = logging.getLogger("run_eval")

    # 1. 创建 LLM adapter
    try:
        from agent.config.loader import ConfigLoader

        loader = ConfigLoader()
        if profile_id is not None:
            profile = loader.get_profile(profile_id)
        else:
            profile = loader.get_active_profile()

        logger.info("使用 profile: %s (model=%s)", profile.get("name", "?"), profile.get("model", "?"))

        from agent.models import create_model_adapter

        adapter = create_model_adapter(profile)
    except Exception as exc:
        logger.error("无法创建 LLM adapter: %s", exc)
        logger.error("请确保配置文件正确，或指定 --profile")
        return 1

    # 2. 加载任务 & 运行
    runner = EvalRunner()
    runner.load()
    logger.info("已加载 %d 个任务", len(runner.tasks))

    if category:
        logger.info("过滤类别: %s", category)

    transcripts = await runner.run_all(
        model_adapter=adapter,
        category=category,
        timeout=120.0,
    )
    logger.info("执行完成，共 %d 个 Transcript", len(transcripts))

    # 3. 评分
    grader = TraceGrader()
    task_map = {t.id: t for t in runner.tasks}
    grades = []
    for tr in transcripts:
        task = task_map.get(tr.task_id)
        if task:
            grade = grader.grade(tr, task)
            grade.details["category"] = task.category
            grades.append(grade)

    # 4. 报告
    summary = reporter.summarize(grades)
    tag_str = tag or (category or "all")
    out_path = reporter.write_report(grades, tag=tag_str)

    # 5. 终端输出
    print("\n" + "=" * 60)
    print("  Agent 评估报告")
    print("=" * 60)
    print(f"  总数: {summary['total']}")
    print(f"  通过: {summary['passed']}")
    print(f"  通过率: {summary['pass_rate']:.1%}")
    print(f"  平均分: {summary['avg_score']:.3f}")
    print()

    if summary.get("by_category"):
        print("  按类别:")
        for cat, stats in sorted(summary["by_category"].items()):
            print(f"    {cat:15s}  {stats['passed']}/{stats['total']}  avg={stats['avg_score']:.3f}")
        print()

    # 逐条结果
    print("  详细结果:")
    for g in grades:
        status = "PASS" if g.passed else "FAIL"
        print(f"    [{status}] {g.task_id:20s}  score={g.score:.3f}  {g.details.get('missing_tools', [])}")
    print()
    print(f"  报告已保存: {out_path}")
    print("=" * 60 + "\n")

    return 0 if summary["pass_rate"] >= 0.5 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 评估工具")
    parser.add_argument("--profile", type=int, default=None, help="Profile ID")
    parser.add_argument("--category", type=str, default=None, help="只跑指定类别")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--tag", type=str, default="", help="报告标签")
    parser.add_argument("--list", action="store_true", help="列出任务（不执行）")
    args = parser.parse_args()

    if args.list:
        list_tasks()
        return

    exit_code = asyncio.run(
        run_eval(
            profile_id=args.profile,
            category=args.category,
            verbose=args.verbose,
            tag=args.tag,
        )
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
