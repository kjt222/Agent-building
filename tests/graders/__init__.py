"""Evaluation Graders - Agent 评估框架

参考 Anthropic Agent 评估方法，实现三种 Grader：

1. **Code Grader** (确定性检查)
   - 检查特定输出格式
   - 验证函数调用参数
   - 计算 token 数量
   - 验证结构化输出

2. **Model Grader** (LLM 判断)
   - 使用最强模型评估输出质量
   - 评估摘要是否保留关键信息
   - 评估工具选择是否合理
   - 调用成本高，仅在测试中使用

3. **Human Grader** (人工评估)
   - 用于主观质量评估
   - 用于建立基准
   - 用于验证模型评估准确性

使用方式：
    # 在测试中使用 Code Grader
    from tests.graders import CodeGrader

    result = CodeGrader.check_json_format(output)
    assert result.passed

    # 在测试中使用 Model Grader (需要 pytest 标记)
    @pytest.mark.model_grader
    def test_summary_quality():
        grader = ModelGrader()
        result = grader.evaluate_summary(original, summary)
        assert result.score >= 0.8
"""

from .code_grader import CodeGrader
from .model_grader import ModelGrader, model_grader_available

__all__ = [
    "CodeGrader",
    "ModelGrader",
    "model_grader_available",
]
