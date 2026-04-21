"""Transcript - Agent 执行轨迹的数据结构

每次 Agent 对话产生一条 Transcript，包含多个 Step。
TraceGrader 基于 Transcript 计算指标。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Step:
    """单步执行记录"""

    role: str  # "user" | "assistant" | "tool_call" | "tool_result"
    content: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None


@dataclass
class Transcript:
    """完整的 Agent 执行轨迹"""

    task_id: str
    steps: list[Step] = field(default_factory=list)
    final_answer: str = ""
    total_tokens: int = 0
    total_latency_ms: float = 0.0

    @property
    def tool_calls(self) -> list[Step]:
        return [s for s in self.steps if s.role == "tool_call"]

    @property
    def tool_errors(self) -> list[Step]:
        return [s for s in self.steps if s.role == "tool_result" and s.error]

    @property
    def tool_names_called(self) -> list[str]:
        return [s.tool_name for s in self.tool_calls if s.tool_name]
