"""端到端评估 Pipeline 测试

A. 纯逻辑测试（unit，无 API 调用）
   - 用 mock AgentExecutor 构造 Transcript
   - TraceGrader 评分 → Reporter 汇总
   - 验证完整 pipeline 不报错

B. 真实评估测试（integration + model_grader 标记）
   - 用真实 LLM adapter 跑代表性任务
   - TraceGrader + CodeGrader 评分
   - Reporter 输出 JSON
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.agent_eval.runner import EvalRunner, TaskDefinition, load_tasks
from tests.agent_eval.transcript import Step, Transcript
from tests.agent_eval.trace_grader import TraceGrader, GradeResult
from tests.agent_eval import reporter


# ─── helpers ────────────────────────────────────────────────────────


def _make_transcript(task_id: str, tool_calls: list[str], final: str) -> Transcript:
    """快速构造一个 Transcript"""
    steps = []
    for name in tool_calls:
        steps.append(Step(role="tool_call", tool_name=name, tool_args={}))
        steps.append(Step(role="tool_result", tool_name=name, content="ok"))
    steps.append(Step(role="assistant", content=final))
    return Transcript(task_id=task_id, steps=steps, final_answer=final)


def _make_task(
    task_id: str,
    category: str = "test",
    must_call: list[str] | None = None,
    must_not_call: list[str] | None = None,
    keywords: list[str] | None = None,
) -> TaskDefinition:
    return TaskDefinition(
        id=task_id,
        category=category,
        user_message="test prompt",
        expected_trace={
            "must_call": must_call or [],
            "must_not_call": must_not_call or [],
            "max_steps": 10,
        },
        expected_answer={"contains_keywords": keywords or []},
    )


# ─── A. 纯逻辑测试 ─────────────────────────────────────────────────


class TestTraceGraderLogic:
    """TraceGrader 评分逻辑"""

    def test_perfect_score(self):
        task = _make_task("t1", must_call=["search_knowledge_base"], keywords=["光刻"])
        transcript = _make_transcript("t1", ["search_knowledge_base"], "光刻技术说明")
        result = TraceGrader().grade(transcript, task)
        assert result.passed
        assert result.score >= 0.9

    def test_missing_tool_fails(self):
        task = _make_task("t2", must_call=["search_knowledge_base"])
        transcript = _make_transcript("t2", [], "没有调用工具")
        result = TraceGrader().grade(transcript, task)
        assert not result.passed
        assert "search_knowledge_base" in result.details["missing_tools"]

    def test_forbidden_tool_fails(self):
        task = _make_task("t3", must_not_call=["remember_fact"])
        transcript = _make_transcript("t3", ["remember_fact"], "不该记忆")
        result = TraceGrader().grade(transcript, task)
        assert not result.passed
        assert "remember_fact" in result.details["forbidden_tools"]

    def test_keyword_mismatch_lowers_score(self):
        task = _make_task("t4", keywords=["光刻", "掩膜", "曝光"])
        transcript = _make_transcript("t4", [], "光刻相关内容")
        result = TraceGrader().grade(transcript, task)
        # 只匹配 1/3 关键词
        assert result.score < 0.5

    def test_no_constraints_passes(self):
        task = _make_task("t5")
        transcript = _make_transcript("t5", [], "任意回答")
        result = TraceGrader().grade(transcript, task)
        assert result.passed
        assert result.score == 1.0


class TestReporterLogic:
    """Reporter 汇总逻辑"""

    def test_summarize_empty(self):
        s = reporter.summarize([])
        assert s["total"] == 0
        assert s["pass_rate"] == 0.0

    def test_summarize_mixed(self):
        results = [
            GradeResult(task_id="a", passed=True, score=0.9, details={"category": "kb"}),
            GradeResult(task_id="b", passed=False, score=0.3, details={"category": "kb"}),
            GradeResult(task_id="c", passed=True, score=1.0, details={"category": "chat"}),
        ]
        s = reporter.summarize(results)
        assert s["total"] == 3
        assert s["passed"] == 2
        assert s["by_category"]["kb"]["total"] == 2
        assert s["by_category"]["chat"]["passed"] == 1

    def test_write_report(self, tmp_path):
        results = [
            GradeResult(task_id="x", passed=True, score=0.8, details={"category": "test"}),
        ]
        # 临时覆盖输出目录
        original_dir = reporter.RESULTS_DIR
        reporter.RESULTS_DIR = tmp_path
        try:
            out = reporter.write_report(results, tag="unit_test")
            assert out.exists()
            import json
            data = json.loads(out.read_text(encoding="utf-8"))
            assert data["summary"]["total"] == 1
            assert len(data["details"]) == 1
        finally:
            reporter.RESULTS_DIR = original_dir


class TestYamlLoading:
    """YAML 任务加载"""

    def test_load_all_tasks(self):
        tasks_dir = Path(__file__).parent / "fixtures" / "agent_tasks"
        tasks = load_tasks(tasks_dir)
        assert len(tasks) >= 20
        ids = [t.id for t in tasks]
        assert "kb_search_001" in ids
        assert "chat_001" in ids
        assert "multi_001" in ids

    def test_all_tasks_have_required_fields(self):
        tasks_dir = Path(__file__).parent / "fixtures" / "agent_tasks"
        tasks = load_tasks(tasks_dir)
        for t in tasks:
            assert t.id, f"Task missing id"
            assert t.category, f"Task {t.id} missing category"
            assert t.user_message, f"Task {t.id} missing user_message"
            assert isinstance(t.expected_trace, dict), f"Task {t.id} bad expected_trace"
            assert isinstance(t.graders, list), f"Task {t.id} bad graders"

    def test_categories_coverage(self):
        tasks_dir = Path(__file__).parent / "fixtures" / "agent_tasks"
        tasks = load_tasks(tasks_dir)
        categories = {t.category for t in tasks}
        assert "kb_retrieval" in categories
        assert "memory" in categories
        assert "system" in categories
        assert "no_tool" in categories
        assert "multi_tool" in categories


class TestStepMapping:
    """AgentStep → Transcript.Step 映射"""

    def test_tool_call_mapping(self):
        from tests.agent_eval.runner import _step_to_transcript_step

        # 模拟 AgentStep
        mock_step = MagicMock()
        mock_step.step_type = "tool_call"
        mock_step.content = {"id": "c1", "name": "search_knowledge_base", "arguments": {"query": "test"}}

        result = _step_to_transcript_step(mock_step)
        assert result is not None
        assert result.role == "tool_call"
        assert result.tool_name == "search_knowledge_base"
        assert result.tool_args == {"query": "test"}

    def test_tool_result_mapping(self):
        from tests.agent_eval.runner import _step_to_transcript_step

        mock_step = MagicMock()
        mock_step.step_type = "tool_result"
        mock_step.content = {"name": "search_knowledge_base", "result": {"output": "found 3 results"}}

        result = _step_to_transcript_step(mock_step)
        assert result is not None
        assert result.role == "tool_result"
        assert result.tool_name == "search_knowledge_base"
        assert result.error is None

    def test_tool_result_with_error(self):
        from tests.agent_eval.runner import _step_to_transcript_step

        mock_step = MagicMock()
        mock_step.step_type = "tool_result"
        mock_step.content = {"name": "bad_tool", "result": {"error": "not found"}}

        result = _step_to_transcript_step(mock_step)
        assert result is not None
        assert result.error is not None

    def test_response_mapping(self):
        from tests.agent_eval.runner import _step_to_transcript_step

        mock_step = MagicMock()
        mock_step.step_type = "response"
        mock_step.content = "这是最终回答"

        result = _step_to_transcript_step(mock_step)
        assert result is not None
        assert result.role == "assistant"
        assert result.content == "这是最终回答"

    def test_thinking_skipped(self):
        from tests.agent_eval.runner import _step_to_transcript_step

        mock_step = MagicMock()
        mock_step.step_type = "thinking"
        mock_step.content = "let me think..."

        result = _step_to_transcript_step(mock_step)
        assert result is None

    def test_compaction_skipped(self):
        from tests.agent_eval.runner import _step_to_transcript_step

        mock_step = MagicMock()
        mock_step.step_type = "compaction"
        mock_step.content = {}

        result = _step_to_transcript_step(mock_step)
        assert result is None


class TestEvalRunnerWithMock:
    """用 mock AgentExecutor 测试 EvalRunner 收集逻辑"""

    @pytest.mark.asyncio
    async def test_run_single_collects_transcript(self):
        """验证 run_single 正确收集 AgentStep 到 Transcript"""
        from agent.core.executor import AgentStep

        # 创建 mock model adapter
        mock_adapter = MagicMock()

        # 模拟 AgentExecutor.run 返回的 async generator
        async def mock_run(prompt, messages=None, system_prompt=None, **kwargs):
            yield AgentStep("thinking", "让我思考...")
            yield AgentStep("tool_call", {"id": "c1", "name": "search_knowledge_base", "arguments": {"query": prompt}})
            yield AgentStep("tool_result", {"tool_call_id": "c1", "name": "search_knowledge_base", "result": {"output": "找到了结果"}})
            yield AgentStep("response", "根据搜索结果，这是回答")

        task = _make_task("mock_1", category="kb_retrieval", must_call=["search_knowledge_base"])

        runner = EvalRunner()

        # Patch AgentExecutor to use our mock
        import unittest.mock as um
        with um.patch("tests.agent_eval.runner.AgentExecutor") as MockExec:
            instance = MockExec.return_value
            instance.run = mock_run
            transcript = await runner.run_single(task, mock_adapter)

        assert transcript.task_id == "mock_1"
        assert transcript.final_answer == "根据搜索结果，这是回答"
        # thinking 被跳过，应有 3 个 steps: tool_call, tool_result, response
        assert len(transcript.steps) == 3
        assert transcript.steps[0].role == "tool_call"
        assert transcript.steps[1].role == "tool_result"
        assert transcript.steps[2].role == "assistant"
        assert "search_knowledge_base" in transcript.tool_names_called

    @pytest.mark.asyncio
    async def test_run_all_processes_multiple_tasks(self):
        """验证 run_all 处理多个任务"""
        from agent.core.executor import AgentStep

        call_count = 0

        async def mock_run(prompt, messages=None, system_prompt=None, **kwargs):
            nonlocal call_count
            call_count += 1
            yield AgentStep("response", f"回答 {call_count}")

        runner = EvalRunner()
        runner.tasks = [
            _make_task("a1", category="no_tool"),
            _make_task("a2", category="no_tool"),
            _make_task("a3", category="no_tool"),
        ]

        mock_adapter = MagicMock()
        import unittest.mock as um
        with um.patch("tests.agent_eval.runner.AgentExecutor") as MockExec:
            instance = MockExec.return_value
            instance.run = mock_run
            results = await runner.run_all(mock_adapter)

        assert len(results) == 3
        assert results[0].task_id == "a1"
        assert results[2].final_answer == "回答 3"

    @pytest.mark.asyncio
    async def test_run_all_with_category_filter(self):
        """验证 category 过滤"""
        from agent.core.executor import AgentStep

        async def mock_run(prompt, messages=None, system_prompt=None, **kwargs):
            yield AgentStep("response", "ok")

        runner = EvalRunner()
        runner.tasks = [
            _make_task("k1", category="kb_retrieval"),
            _make_task("c1", category="no_tool"),
            _make_task("k2", category="kb_retrieval"),
        ]

        mock_adapter = MagicMock()
        import unittest.mock as um
        with um.patch("tests.agent_eval.runner.AgentExecutor") as MockExec:
            instance = MockExec.return_value
            instance.run = mock_run
            results = await runner.run_all(mock_adapter, category="kb_retrieval")

        assert len(results) == 2


class TestFullPipeline:
    """Runner → TraceGrader → Reporter 完整 pipeline"""

    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self, tmp_path):
        """完整 pipeline: mock 执行 → 评分 → 报告"""
        from agent.core.executor import AgentStep

        async def mock_run(prompt, messages=None, system_prompt=None, **kwargs):
            yield AgentStep("tool_call", {"id": "c1", "name": "search_knowledge_base", "arguments": {"query": prompt}})
            yield AgentStep("tool_result", {"tool_call_id": "c1", "name": "search_knowledge_base", "result": {"output": "光刻相关内容"}})
            yield AgentStep("response", "光刻技术包含掩膜和曝光步骤")

        tasks = [
            _make_task("p1", category="kb_retrieval", must_call=["search_knowledge_base"], keywords=["光刻"]),
            _make_task("p2", category="no_tool", must_not_call=["search_knowledge_base"]),
        ]

        runner = EvalRunner()
        runner.tasks = tasks

        mock_adapter = MagicMock()
        import unittest.mock as um
        with um.patch("tests.agent_eval.runner.AgentExecutor") as MockExec:
            instance = MockExec.return_value
            instance.run = mock_run
            transcripts = await runner.run_all(mock_adapter)

        # 评分
        grader = TraceGrader()
        grades = []
        for transcript, task in zip(transcripts, tasks):
            grade = grader.grade(transcript, task)
            grade.details["category"] = task.category
            grades.append(grade)

        # p1: 调用了 search_knowledge_base，包含"光刻" → 应该通过
        assert grades[0].passed

        # p2: 不该调用 search_knowledge_base 但调用了 → 应该失败
        assert not grades[1].passed

        # 报告
        original_dir = reporter.RESULTS_DIR
        reporter.RESULTS_DIR = tmp_path
        try:
            summary = reporter.summarize(grades)
            assert summary["total"] == 2
            assert summary["passed"] == 1

            out = reporter.write_report(grades, tag="pipeline_test")
            assert out.exists()
        finally:
            reporter.RESULTS_DIR = original_dir


# ─── B. 真实评估测试（需要 API） ────────────────────────────────────


@pytest.mark.integration
@pytest.mark.slow
class TestRealEvaluation:
    """真实 LLM 评估（需要配置环境）"""

    @pytest.mark.asyncio
    async def test_real_eval_sample(self):
        """用真实 LLM 跑 3 个代表性任务"""
        pytest.skip("需要真实 LLM 环境配置，手动运行: python -m tests.run_eval")
