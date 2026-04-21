"""EvalRunner - 批量执行 YAML 任务定义并收集 Transcript

Phase 2.5.1: 接入 AgentExecutor，执行任务，收集 Transcript
- 加载 fixtures/agent_tasks/*.yaml
- 对每条任务调用 AgentExecutor
- 收集 Transcript 并传递给 TraceGrader
- 支持单任务 / 批量执行
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.core.executor import AgentConfig, AgentExecutor

from .transcript import Step, Transcript

logger = logging.getLogger(__name__)


@dataclass
class TaskDefinition:
    """一条评估任务的定义（从 YAML 加载）"""

    id: str
    category: str
    user_message: str
    expected_trace: dict[str, Any] = field(default_factory=dict)
    expected_answer: dict[str, Any] = field(default_factory=dict)
    graders: list[str] = field(default_factory=lambda: ["code"])


def load_tasks(tasks_dir: Path) -> list[TaskDefinition]:
    """从目录加载所有 YAML 任务定义"""
    import yaml

    tasks = []
    for path in sorted(tasks_dir.glob("*.yaml")):
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            tasks.extend(TaskDefinition(**item) for item in data)
        else:
            tasks.append(TaskDefinition(**data))
    return tasks


def _step_to_transcript_step(agent_step) -> Step | None:
    """将 AgentStep 映射为 Transcript.Step，不相关类型返回 None"""
    st = agent_step.step_type
    content = agent_step.content

    if st == "thinking" or st == "compaction":
        # 跳过：不计入 Transcript
        return None

    if st == "tool_call":
        # content 是 dict: {"id": ..., "name": ..., "arguments": {...}}
        if isinstance(content, dict):
            return Step(
                role="tool_call",
                content="",
                tool_name=content.get("name"),
                tool_args=content.get("arguments"),
            )
        return Step(role="tool_call", content=str(content))

    if st == "tool_result":
        # content 是 dict: {"tool_call_id": ..., "name": ..., "result": {...}}
        if isinstance(content, dict):
            result_data = content.get("result", {})
            error = None
            if isinstance(result_data, dict) and result_data.get("error"):
                error = str(result_data["error"])
            return Step(
                role="tool_result",
                content=str(result_data.get("output", result_data)),
                tool_name=content.get("name"),
                error=error,
            )
        return Step(role="tool_result", content=str(content))

    if st == "response":
        return Step(
            role="assistant",
            content=content if isinstance(content, str) else str(content),
        )

    if st == "error":
        return Step(
            role="assistant",
            content=str(content),
            error=str(content),
        )

    # 未知类型，记录但不丢弃
    logger.warning("Unknown AgentStep type: %s", st)
    return Step(role="assistant", content=str(content))


class EvalRunner:
    """批量运行评估任务"""

    def __init__(self, tasks_dir: Path | None = None) -> None:
        default_dir = Path(__file__).parent.parent / "fixtures" / "agent_tasks"
        self.tasks_dir = tasks_dir or default_dir
        self.tasks: list[TaskDefinition] = []
        self.results: list[Transcript] = []

    def load(self) -> None:
        self.tasks = load_tasks(self.tasks_dir)

    async def run_single(
        self,
        task: TaskDefinition,
        model_adapter,
        system_prompt: str | None = None,
        tool_setup_fn: Callable | None = None,
        timeout: float = 120.0,
    ) -> Transcript:
        """执行单个任务，返回 Transcript

        Args:
            task: 任务定义
            model_adapter: LLM adapter 实例
            system_prompt: 可选系统提示
            tool_setup_fn: 可选工具注册回调，签名 fn() -> None
            timeout: 单任务超时（秒）
        """
        # 每次新建 executor，避免状态污染
        config = AgentConfig(max_iterations=20, enable_compaction=False)
        executor = AgentExecutor(model_adapter, config=config)

        # 注册工具（如果提供回调）
        if tool_setup_fn:
            tool_setup_fn()

        steps: list[Step] = []
        final_answer = ""
        t0 = time.perf_counter()

        try:
            async with asyncio.timeout(timeout):
                async for agent_step in executor.run(
                    task.user_message, system_prompt=system_prompt
                ):
                    ts = _step_to_transcript_step(agent_step)
                    if ts is not None:
                        steps.append(ts)
                    # 记录最后一个 response 作为 final_answer
                    if agent_step.step_type == "response":
                        content = agent_step.content
                        final_answer = content if isinstance(content, str) else str(content)
        except (asyncio.TimeoutError, TimeoutError):
            steps.append(Step(role="assistant", content="[TIMEOUT]", error="timeout"))
            final_answer = "[TIMEOUT]"
        except Exception as exc:
            steps.append(Step(role="assistant", content=str(exc), error=str(exc)))
            final_answer = f"[ERROR] {exc}"

        elapsed_ms = (time.perf_counter() - t0) * 1000

        return Transcript(
            task_id=task.id,
            steps=steps,
            final_answer=final_answer,
            total_tokens=0,  # 暂无 token 统计
            total_latency_ms=round(elapsed_ms, 1),
        )

    async def run_all(
        self,
        model_adapter,
        system_prompt: str | None = None,
        tool_setup_fn: Callable | None = None,
        timeout: float = 120.0,
        category: str | None = None,
    ) -> list[Transcript]:
        """执行所有（或指定 category 的）任务并返回 Transcript 列表

        Args:
            model_adapter: LLM adapter 实例
            system_prompt: 可选系统提示
            tool_setup_fn: 可选工具注册回调
            timeout: 单任务超时（秒）
            category: 可选，只跑指定 category
        """
        if not self.tasks:
            self.load()

        tasks = self.tasks
        if category:
            tasks = [t for t in tasks if t.category == category]

        self.results = []
        for i, task in enumerate(tasks, 1):
            logger.info("[%d/%d] Running task: %s", i, len(tasks), task.id)
            transcript = await self.run_single(
                task, model_adapter, system_prompt, tool_setup_fn, timeout
            )
            self.results.append(transcript)
            logger.info(
                "  -> %s  final_answer=%s",
                task.id,
                transcript.final_answer[:80] if transcript.final_answer else "(empty)",
            )

        return self.results
