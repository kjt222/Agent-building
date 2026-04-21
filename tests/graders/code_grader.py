"""Code Grader - 确定性检查

用于快速、确定性的输出验证：
- JSON 格式验证
- 结构化输出检查
- Token 估算验证
- 工具调用参数验证

优点：
- 运行快速（毫秒级）
- 完全确定性，无随机性
- 无 API 调用成本
- 适合 CI/CD 集成

使用场景：
- 验证输出格式正确性
- 检查必需字段存在
- 验证数值范围
- 检查字符串模式
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Pattern


@dataclass
class GradeResult:
    """评估结果"""
    passed: bool
    score: float  # 0.0 - 1.0
    message: str
    details: Optional[Dict[str, Any]] = None


class CodeGrader:
    """确定性代码检查器"""

    # ============================================================
    # JSON 格式验证
    # ============================================================

    @staticmethod
    def check_json_format(text: str) -> GradeResult:
        """检查是否为有效 JSON"""
        try:
            json.loads(text)
            return GradeResult(
                passed=True,
                score=1.0,
                message="Valid JSON"
            )
        except json.JSONDecodeError as e:
            return GradeResult(
                passed=False,
                score=0.0,
                message=f"Invalid JSON: {str(e)}",
                details={"error_position": e.pos}
            )

    @staticmethod
    def check_json_structure(
        data: Dict[str, Any],
        required_fields: List[str],
        optional_fields: List[str] = None
    ) -> GradeResult:
        """
        检查 JSON 结构是否包含必需字段

        Args:
            data: JSON 数据（dict）
            required_fields: 必需字段列表
            optional_fields: 可选字段列表（用于警告未知字段）
        """
        missing = []
        for field in required_fields:
            # 支持嵌套字段，如 "data.items"
            if "." in field:
                parts = field.split(".")
                value = data
                for part in parts:
                    if isinstance(value, dict) and part in value:
                        value = value[part]
                    else:
                        missing.append(field)
                        break
            else:
                if field not in data:
                    missing.append(field)

        if missing:
            return GradeResult(
                passed=False,
                score=(len(required_fields) - len(missing)) / len(required_fields) if required_fields else 0,
                message=f"Missing required fields: {missing}",
                details={"missing_fields": missing}
            )

        return GradeResult(
            passed=True,
            score=1.0,
            message="All required fields present"
        )

    # ============================================================
    # Token 估算验证
    # ============================================================

    @staticmethod
    def estimate_tokens(text: str, chars_per_token: float = 2.5) -> int:
        """
        估算文本的 token 数量

        Args:
            text: 输入文本
            chars_per_token: 每个 token 的平均字符数（中文约2.5）
        """
        if not text:
            return 0
        return int(len(text) / chars_per_token)

    @staticmethod
    def check_token_count(
        text: str,
        min_tokens: int = None,
        max_tokens: int = None
    ) -> GradeResult:
        """
        检查 token 数量是否在范围内

        Args:
            text: 输入文本
            min_tokens: 最小 token 数
            max_tokens: 最大 token 数
        """
        estimated = CodeGrader.estimate_tokens(text)

        if min_tokens and estimated < min_tokens:
            return GradeResult(
                passed=False,
                score=estimated / min_tokens,
                message=f"Token count ({estimated}) below minimum ({min_tokens})",
                details={"estimated_tokens": estimated, "min_tokens": min_tokens}
            )

        if max_tokens and estimated > max_tokens:
            return GradeResult(
                passed=False,
                score=max_tokens / estimated,
                message=f"Token count ({estimated}) exceeds maximum ({max_tokens})",
                details={"estimated_tokens": estimated, "max_tokens": max_tokens}
            )

        return GradeResult(
            passed=True,
            score=1.0,
            message=f"Token count ({estimated}) within range",
            details={"estimated_tokens": estimated}
        )

    # ============================================================
    # 字符串模式验证
    # ============================================================

    @staticmethod
    def check_pattern(
        text: str,
        pattern: str,
        flags: int = 0
    ) -> GradeResult:
        """
        检查文本是否匹配正则表达式

        Args:
            text: 输入文本
            pattern: 正则表达式
            flags: 正则标志（如 re.IGNORECASE）
        """
        if re.search(pattern, text, flags):
            return GradeResult(
                passed=True,
                score=1.0,
                message=f"Pattern '{pattern}' found"
            )
        return GradeResult(
            passed=False,
            score=0.0,
            message=f"Pattern '{pattern}' not found"
        )

    @staticmethod
    def check_contains(
        text: str,
        must_contain: List[str],
        case_sensitive: bool = True
    ) -> GradeResult:
        """
        检查文本是否包含所有必需内容

        Args:
            text: 输入文本
            must_contain: 必须包含的字符串列表
            case_sensitive: 是否区分大小写
        """
        if not case_sensitive:
            text = text.lower()
            must_contain = [s.lower() for s in must_contain]

        missing = [s for s in must_contain if s not in text]

        if missing:
            return GradeResult(
                passed=False,
                score=(len(must_contain) - len(missing)) / len(must_contain),
                message=f"Missing required content: {missing}",
                details={"missing": missing}
            )

        return GradeResult(
            passed=True,
            score=1.0,
            message="All required content found"
        )

    @staticmethod
    def check_not_contains(
        text: str,
        forbidden: List[str],
        case_sensitive: bool = True
    ) -> GradeResult:
        """
        检查文本是否不包含禁止内容

        Args:
            text: 输入文本
            forbidden: 不应包含的字符串列表
            case_sensitive: 是否区分大小写
        """
        if not case_sensitive:
            text = text.lower()
            forbidden = [s.lower() for s in forbidden]

        found = [s for s in forbidden if s in text]

        if found:
            return GradeResult(
                passed=False,
                score=(len(forbidden) - len(found)) / len(forbidden),
                message=f"Found forbidden content: {found}",
                details={"found": found}
            )

        return GradeResult(
            passed=True,
            score=1.0,
            message="No forbidden content found"
        )

    # ============================================================
    # 数值范围验证
    # ============================================================

    @staticmethod
    def check_value_range(
        value: float,
        min_value: float = None,
        max_value: float = None
    ) -> GradeResult:
        """
        检查数值是否在范围内

        Args:
            value: 数值
            min_value: 最小值
            max_value: 最大值
        """
        if min_value is not None and value < min_value:
            return GradeResult(
                passed=False,
                score=0.0,
                message=f"Value {value} below minimum {min_value}"
            )

        if max_value is not None and value > max_value:
            return GradeResult(
                passed=False,
                score=0.0,
                message=f"Value {value} exceeds maximum {max_value}"
            )

        return GradeResult(
            passed=True,
            score=1.0,
            message=f"Value {value} within range"
        )

    # ============================================================
    # 工具调用验证
    # ============================================================

    @staticmethod
    def check_tool_call(
        tool_call: Dict[str, Any],
        expected_name: str,
        required_params: List[str] = None
    ) -> GradeResult:
        """
        检查工具调用是否正确

        Args:
            tool_call: 工具调用数据 {"name": "xxx", "arguments": {...}}
            expected_name: 期望的工具名称
            required_params: 必需的参数列表
        """
        # 检查工具名称
        if tool_call.get("name") != expected_name:
            return GradeResult(
                passed=False,
                score=0.0,
                message=f"Wrong tool called: {tool_call.get('name')}, expected: {expected_name}"
            )

        # 检查必需参数
        if required_params:
            args = tool_call.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    return GradeResult(
                        passed=False,
                        score=0.5,
                        message="Tool arguments are not valid JSON"
                    )

            missing = [p for p in required_params if p not in args]
            if missing:
                return GradeResult(
                    passed=False,
                    score=0.5 + 0.5 * (len(required_params) - len(missing)) / len(required_params),
                    message=f"Missing required parameters: {missing}",
                    details={"missing_params": missing}
                )

        return GradeResult(
            passed=True,
            score=1.0,
            message="Tool call correct"
        )

    # ============================================================
    # 复合验证
    # ============================================================

    @staticmethod
    def check_all(results: List[GradeResult]) -> GradeResult:
        """
        检查所有结果是否都通过

        Args:
            results: GradeResult 列表
        """
        failed = [r for r in results if not r.passed]

        if failed:
            return GradeResult(
                passed=False,
                score=sum(r.score for r in results) / len(results),
                message=f"{len(failed)} of {len(results)} checks failed",
                details={
                    "failed_messages": [r.message for r in failed]
                }
            )

        return GradeResult(
            passed=True,
            score=1.0,
            message=f"All {len(results)} checks passed"
        )

    @staticmethod
    def check_any(results: List[GradeResult]) -> GradeResult:
        """
        检查是否有任意结果通过

        Args:
            results: GradeResult 列表
        """
        passed = [r for r in results if r.passed]

        if passed:
            return GradeResult(
                passed=True,
                score=max(r.score for r in results),
                message=f"{len(passed)} of {len(results)} checks passed"
            )

        return GradeResult(
            passed=False,
            score=0.0,
            message="No checks passed",
            details={
                "all_messages": [r.message for r in results]
            }
        )
