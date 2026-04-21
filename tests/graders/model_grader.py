"""Model Grader - LLM 判断

使用最强模型评估 Agent 输出质量：
- 摘要质量评估
- 工具选择合理性
- 回答相关性
- 信息完整性

特点：
- 使用最强模型（如 Claude 3.5 Sonnet / GPT-4o）
- 调用成本较高，仅在测试中使用
- 支持 pytest 标记控制是否运行
- 返回结构化评分和解释

使用方式：
    # 需要 pytest 标记
    @pytest.mark.model_grader
    def test_summary_quality():
        grader = ModelGrader()
        result = grader.evaluate_summary(
            original="长对话内容...",
            summary="压缩后的摘要..."
        )
        assert result.score >= 0.8
"""

import os
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ModelGradeResult:
    """Model Grader 评估结果"""
    passed: bool
    score: float  # 0.0 - 1.0
    explanation: str  # 模型的解释
    criteria_scores: Dict[str, float]  # 各评估标准的分数
    raw_response: Optional[str] = None  # 原始模型响应


def model_grader_available() -> bool:
    """检查 Model Grader 是否可用（有 API key）"""
    return bool(
        os.environ.get("OPENAI_API_KEY") or
        os.environ.get("ANTHROPIC_API_KEY") or
        os.environ.get("AZURE_OPENAI_API_KEY")
    )


class ModelGrader:
    """LLM 评估器"""

    def __init__(
        self,
        provider: str = None,
        model: str = None,
        api_key: str = None
    ):
        """
        初始化 Model Grader

        Args:
            provider: LLM 提供商 ("openai", "anthropic", "azure")，默认自动检测
            model: 模型名称，默认使用最强模型
            api_key: API 密钥，默认从环境变量获取
        """
        self.provider = provider or self._detect_provider()
        self.model = model or self._get_default_model()
        self.api_key = api_key

        self._client = None

    def _detect_provider(self) -> str:
        """自动检测可用的 LLM 提供商"""
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "anthropic"
        if os.environ.get("OPENAI_API_KEY"):
            return "openai"
        if os.environ.get("AZURE_OPENAI_API_KEY"):
            return "azure"
        raise RuntimeError(
            "No LLM API key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "or AZURE_OPENAI_API_KEY environment variable."
        )

    def _get_default_model(self) -> str:
        """获取默认（最强）模型"""
        defaults = {
            "anthropic": "claude-sonnet-4-20250514",
            "openai": "gpt-4o",
            "azure": "gpt-4o"
        }
        return defaults.get(self.provider, "gpt-4o")

    def _get_client(self):
        """获取或创建 LLM 客户端"""
        if self._client:
            return self._client

        if self.provider == "anthropic":
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self.api_key)
        elif self.provider == "openai":
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        elif self.provider == "azure":
            from openai import AzureOpenAI
            self._client = AzureOpenAI(
                api_key=self.api_key or os.environ.get("AZURE_OPENAI_API_KEY"),
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
                azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT")
            )
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

        return self._client

    def _call_llm(self, system: str, user: str) -> str:
        """调用 LLM 获取响应"""
        client = self._get_client()

        if self.provider == "anthropic":
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}]
            )
            return response.content[0].text
        else:
            # OpenAI / Azure
            response = client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ]
            )
            return response.choices[0].message.content

    def _parse_evaluation(self, response: str) -> Dict[str, Any]:
        """解析评估响应为结构化数据"""
        # 尝试提取 JSON
        try:
            # 查找 JSON 块
            json_match = response
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                json_match = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                json_match = response[start:end].strip()

            return json.loads(json_match)
        except json.JSONDecodeError:
            # 如果无法解析 JSON，返回默认结构
            return {
                "score": 0.5,
                "explanation": response,
                "criteria": {}
            }

    # ============================================================
    # 摘要质量评估
    # ============================================================

    def evaluate_summary(
        self,
        original: str,
        summary: str,
        context: str = None
    ) -> ModelGradeResult:
        """
        评估摘要质量

        Args:
            original: 原始完整内容
            summary: 压缩后的摘要
            context: 可选的上下文说明

        Returns:
            评估结果，包含分数和解释
        """
        system_prompt = """你是一个专业的摘要质量评估员。评估以下摘要的质量。

评估标准：
1. **信息保留** (0-1): 关键信息是否保留？重要事实是否丢失？
2. **准确性** (0-1): 摘要内容是否准确反映原文？有无歪曲或错误？
3. **简洁性** (0-1): 是否足够简洁？有无冗余信息？
4. **可理解性** (0-1): 摘要是否清晰易懂？逻辑是否连贯？

请返回 JSON 格式的评估结果：
```json
{
    "score": 0.85,
    "explanation": "整体评估说明...",
    "criteria": {
        "information_retention": 0.9,
        "accuracy": 0.8,
        "conciseness": 0.85,
        "clarity": 0.85
    },
    "key_points_preserved": ["要点1", "要点2"],
    "key_points_missing": ["遗漏的要点"]
}
```"""

        user_prompt = f"""## 原始内容
{original[:3000]}{'...(已截断)' if len(original) > 3000 else ''}

## 摘要
{summary}

{f'## 上下文说明\n{context}' if context else ''}

请评估这个摘要的质量。"""

        try:
            response = self._call_llm(system_prompt, user_prompt)
            result = self._parse_evaluation(response)

            score = result.get("score", 0.5)
            criteria = result.get("criteria", {})

            return ModelGradeResult(
                passed=score >= 0.7,  # 70% 为及格线
                score=score,
                explanation=result.get("explanation", ""),
                criteria_scores=criteria,
                raw_response=response
            )
        except Exception as e:
            return ModelGradeResult(
                passed=False,
                score=0.0,
                explanation=f"评估失败: {str(e)}",
                criteria_scores={}
            )

    # ============================================================
    # 工具选择评估
    # ============================================================

    def evaluate_tool_selection(
        self,
        user_query: str,
        selected_tool: str,
        available_tools: List[str],
        tool_descriptions: Dict[str, str] = None
    ) -> ModelGradeResult:
        """
        评估工具选择是否合理

        Args:
            user_query: 用户的问题/请求
            selected_tool: Agent 选择的工具
            available_tools: 可用工具列表
            tool_descriptions: 工具描述字典

        Returns:
            评估结果
        """
        system_prompt = """你是一个 AI Agent 工具选择评估员。评估 Agent 选择的工具是否合理。

评估标准：
1. **相关性** (0-1): 选择的工具是否与用户请求相关？
2. **最优性** (0-1): 是否选择了最合适的工具？有没有更好的选择？
3. **完整性** (0-1): 单个工具是否能完成任务？是否需要组合？

请返回 JSON 格式的评估结果：
```json
{
    "score": 0.9,
    "explanation": "工具选择合理，因为...",
    "criteria": {
        "relevance": 0.95,
        "optimality": 0.85,
        "completeness": 0.9
    },
    "suggested_tool": "如果有更好的选择",
    "reasoning": "选择理由分析"
}
```"""

        tools_info = "\n".join([
            f"- {tool}: {tool_descriptions.get(tool, '无描述')}"
            for tool in available_tools
        ]) if tool_descriptions else "\n".join([f"- {tool}" for tool in available_tools])

        user_prompt = f"""## 用户请求
{user_query}

## 可用工具
{tools_info}

## Agent 选择的工具
{selected_tool}

请评估这个工具选择是否合理。"""

        try:
            response = self._call_llm(system_prompt, user_prompt)
            result = self._parse_evaluation(response)

            score = result.get("score", 0.5)
            criteria = result.get("criteria", {})

            return ModelGradeResult(
                passed=score >= 0.7,
                score=score,
                explanation=result.get("explanation", ""),
                criteria_scores=criteria,
                raw_response=response
            )
        except Exception as e:
            return ModelGradeResult(
                passed=False,
                score=0.0,
                explanation=f"评估失败: {str(e)}",
                criteria_scores={}
            )

    # ============================================================
    # 回答质量评估
    # ============================================================

    def evaluate_response(
        self,
        user_query: str,
        response: str,
        context: str = None,
        expected_behavior: str = None
    ) -> ModelGradeResult:
        """
        评估 Agent 回答质量

        Args:
            user_query: 用户的问题
            response: Agent 的回答
            context: 可用的上下文信息（如知识库内容）
            expected_behavior: 期望的行为描述

        Returns:
            评估结果
        """
        system_prompt = """你是一个 AI 回答质量评估员。评估 AI 助手的回答质量。

评估标准：
1. **相关性** (0-1): 回答是否切中问题要点？
2. **准确性** (0-1): 回答内容是否准确？有无错误信息？
3. **完整性** (0-1): 回答是否充分？是否遗漏重要内容？
4. **有用性** (0-1): 回答是否对用户有帮助？

请返回 JSON 格式的评估结果：
```json
{
    "score": 0.85,
    "explanation": "回答质量评估...",
    "criteria": {
        "relevance": 0.9,
        "accuracy": 0.8,
        "completeness": 0.85,
        "helpfulness": 0.85
    },
    "strengths": ["优点1", "优点2"],
    "weaknesses": ["待改进点"]
}
```"""

        user_prompt = f"""## 用户问题
{user_query}

## AI 回答
{response}

{f'## 可用上下文\n{context[:2000]}' if context else ''}
{f'## 期望行为\n{expected_behavior}' if expected_behavior else ''}

请评估这个回答的质量。"""

        try:
            llm_response = self._call_llm(system_prompt, user_prompt)
            result = self._parse_evaluation(llm_response)

            score = result.get("score", 0.5)
            criteria = result.get("criteria", {})

            return ModelGradeResult(
                passed=score >= 0.7,
                score=score,
                explanation=result.get("explanation", ""),
                criteria_scores=criteria,
                raw_response=llm_response
            )
        except Exception as e:
            return ModelGradeResult(
                passed=False,
                score=0.0,
                explanation=f"评估失败: {str(e)}",
                criteria_scores={}
            )

    # ============================================================
    # 自定义评估
    # ============================================================

    def evaluate_custom(
        self,
        content: str,
        criteria: Dict[str, str],
        passing_score: float = 0.7
    ) -> ModelGradeResult:
        """
        自定义评估

        Args:
            content: 要评估的内容
            criteria: 评估标准字典 {"标准名": "标准描述"}
            passing_score: 及格分数

        Returns:
            评估结果
        """
        criteria_text = "\n".join([
            f"{i+1}. **{name}** (0-1): {desc}"
            for i, (name, desc) in enumerate(criteria.items())
        ])

        criteria_json = {name: 0.0 for name in criteria}

        system_prompt = f"""你是一个专业评估员。根据以下标准评估内容。

评估标准：
{criteria_text}

请返回 JSON 格式的评估结果：
```json
{{
    "score": 0.85,
    "explanation": "整体评估说明...",
    "criteria": {json.dumps(criteria_json, ensure_ascii=False)}
}}
```"""

        user_prompt = f"""## 待评估内容
{content}

请根据评估标准进行评估。"""

        try:
            response = self._call_llm(system_prompt, user_prompt)
            result = self._parse_evaluation(response)

            score = result.get("score", 0.5)
            criteria_scores = result.get("criteria", {})

            return ModelGradeResult(
                passed=score >= passing_score,
                score=score,
                explanation=result.get("explanation", ""),
                criteria_scores=criteria_scores,
                raw_response=response
            )
        except Exception as e:
            return ModelGradeResult(
                passed=False,
                score=0.0,
                explanation=f"评估失败: {str(e)}",
                criteria_scores={}
            )
