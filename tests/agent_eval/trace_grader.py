"""TraceGrader - 轨迹级评分

基于 Transcript 和 TaskDefinition 计算以下指标：
- tool_error_rate: 工具调用错误率
- redundant_steps: 冗余步骤数
- missing_tools: 未调用的必需工具
- forbidden_tools: 调用了禁止工具
- step_count: 总步骤数 vs max_steps
- keyword_match: 最终答案关键词匹配率
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .runner import TaskDefinition
from .transcript import Transcript


@dataclass
class GradeResult:
    """单条任务的评分结果"""

    task_id: str
    passed: bool
    score: float  # 0.0 ~ 1.0
    details: dict[str, Any]


class TraceGrader:
    """根据 expected_trace 和 expected_answer 评分"""

    def grade(self, transcript: Transcript, task: TaskDefinition) -> GradeResult:
        details: dict[str, Any] = {}
        score = 1.0

        # 工具调用检查
        called = set(transcript.tool_names_called)
        must_call = set(task.expected_trace.get("must_call", []))
        must_not_call = set(task.expected_trace.get("must_not_call", []))
        max_steps = task.expected_trace.get("max_steps", 999)

        missing = must_call - called
        forbidden = must_not_call & called
        details["missing_tools"] = list(missing)
        details["forbidden_tools"] = list(forbidden)

        if missing:
            score -= 0.3 * len(missing)
        if forbidden:
            score -= 0.3 * len(forbidden)

        # 步骤数检查
        step_count = len(transcript.tool_calls)
        details["step_count"] = step_count
        details["max_steps"] = max_steps
        if step_count > max_steps:
            score -= 0.1 * (step_count - max_steps)

        # 错误率
        error_count = len(transcript.tool_errors)
        details["tool_error_count"] = error_count
        if transcript.tool_calls:
            details["tool_error_rate"] = error_count / len(transcript.tool_calls)
            score -= 0.2 * details["tool_error_rate"]

        # 关键词匹配
        keywords = task.expected_answer.get("contains_keywords", [])
        if keywords:
            matched = sum(1 for kw in keywords if kw in transcript.final_answer)
            details["keyword_match"] = matched / len(keywords)
            score *= details["keyword_match"]

        score = max(0.0, min(1.0, score))
        passed = score >= 0.6 and not missing and not forbidden

        return GradeResult(
            task_id=task.id,
            passed=passed,
            score=round(score, 3),
            details=details,
        )
