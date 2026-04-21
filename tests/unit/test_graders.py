"""Tests for Evaluation Graders.

测试 Code Grader 和 Model Grader 功能。

运行方式（unittest）：
    python -m unittest tests.test_graders -v

运行方式（pytest，可选）：
    # 只运行 Code Grader 测试（无需 API）
    pytest tests/test_graders.py

    # 包含 Model Grader 测试（需要 API）
    pytest tests/test_graders.py --run-model-grader
"""

import unittest
import json
import os

# pytest 是可选的，用于更高级的测试功能
try:
    import pytest
    HAS_PYTEST = True
except ImportError:
    HAS_PYTEST = False
    # 创建一个简单的标记装饰器替代
    class pytest:
        class mark:
            @staticmethod
            def model_grader(cls):
                """跳过需要 LLM API 的测试"""
                return unittest.skip("Requires pytest with --run-model-grader")(cls)

from tests.graders import CodeGrader, ModelGrader, model_grader_available


# ============================================================
# Code Grader Tests - 确定性检查
# ============================================================

class TestCodeGraderJSON(unittest.TestCase):
    """Test JSON validation."""

    def test_valid_json(self):
        result = CodeGrader.check_json_format('{"key": "value"}')
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)

    def test_invalid_json(self):
        result = CodeGrader.check_json_format('{invalid}')
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.0)

    def test_json_structure_all_present(self):
        data = {"name": "test", "value": 123, "items": [1, 2, 3]}
        result = CodeGrader.check_json_structure(
            data,
            required_fields=["name", "value"]
        )
        self.assertTrue(result.passed)

    def test_json_structure_missing_field(self):
        data = {"name": "test"}
        result = CodeGrader.check_json_structure(
            data,
            required_fields=["name", "value"]
        )
        self.assertFalse(result.passed)
        self.assertIn("value", result.details["missing_fields"])


class TestCodeGraderTokens(unittest.TestCase):
    """Test token estimation."""

    def test_estimate_tokens_empty(self):
        tokens = CodeGrader.estimate_tokens("")
        self.assertEqual(tokens, 0)

    def test_estimate_tokens_english(self):
        # "Hello world" = 11 chars / 2.5 = ~4 tokens
        tokens = CodeGrader.estimate_tokens("Hello world")
        self.assertTrue(3 <= tokens <= 6)

    def test_estimate_tokens_chinese(self):
        # "你好世界" = 4 chars / 2.5 = ~2 tokens
        tokens = CodeGrader.estimate_tokens("你好世界")
        self.assertTrue(1 <= tokens <= 3)

    def test_token_count_within_range(self):
        text = "x" * 100  # 100 chars / 2.5 = 40 tokens
        result = CodeGrader.check_token_count(text, min_tokens=10, max_tokens=100)
        self.assertTrue(result.passed)

    def test_token_count_below_minimum(self):
        text = "hello"  # ~2 tokens
        result = CodeGrader.check_token_count(text, min_tokens=100)
        self.assertFalse(result.passed)

    def test_token_count_above_maximum(self):
        text = "x" * 1000  # ~400 tokens
        result = CodeGrader.check_token_count(text, max_tokens=100)
        self.assertFalse(result.passed)


class TestCodeGraderPattern(unittest.TestCase):
    """Test pattern matching."""

    def test_pattern_found(self):
        result = CodeGrader.check_pattern("Hello World", r"World")
        self.assertTrue(result.passed)

    def test_pattern_not_found(self):
        result = CodeGrader.check_pattern("Hello World", r"Foo")
        self.assertFalse(result.passed)

    def test_contains_all(self):
        result = CodeGrader.check_contains(
            "Hello World, how are you?",
            must_contain=["Hello", "World"]
        )
        self.assertTrue(result.passed)

    def test_contains_missing(self):
        result = CodeGrader.check_contains(
            "Hello World",
            must_contain=["Hello", "Foo"]
        )
        self.assertFalse(result.passed)
        self.assertIn("Foo", result.details["missing"])

    def test_not_contains_clean(self):
        result = CodeGrader.check_not_contains(
            "Hello World",
            forbidden=["error", "fail"]
        )
        self.assertTrue(result.passed)

    def test_not_contains_found(self):
        result = CodeGrader.check_not_contains(
            "An error occurred",
            forbidden=["error", "fail"]
        )
        self.assertFalse(result.passed)
        self.assertIn("error", result.details["found"])


class TestCodeGraderToolCall(unittest.TestCase):
    """Test tool call validation."""

    def test_correct_tool_call(self):
        tool_call = {
            "name": "search_knowledge_base",
            "arguments": {"query": "光刻技术"}
        }
        result = CodeGrader.check_tool_call(
            tool_call,
            expected_name="search_knowledge_base",
            required_params=["query"]
        )
        self.assertTrue(result.passed)

    def test_wrong_tool_name(self):
        tool_call = {
            "name": "list_memories",
            "arguments": {}
        }
        result = CodeGrader.check_tool_call(
            tool_call,
            expected_name="search_knowledge_base"
        )
        self.assertFalse(result.passed)
        self.assertIn("Wrong tool", result.message)

    def test_missing_required_param(self):
        tool_call = {
            "name": "search_knowledge_base",
            "arguments": {}
        }
        result = CodeGrader.check_tool_call(
            tool_call,
            expected_name="search_knowledge_base",
            required_params=["query"]
        )
        self.assertFalse(result.passed)
        self.assertIn("query", result.details["missing_params"])


class TestCodeGraderComposite(unittest.TestCase):
    """Test composite checks."""

    def test_check_all_passed(self):
        results = [
            CodeGrader.check_json_format('{"valid": true}'),
            CodeGrader.check_pattern("Hello World", r"World"),
        ]
        result = CodeGrader.check_all(results)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)

    def test_check_all_some_failed(self):
        results = [
            CodeGrader.check_json_format('{"valid": true}'),
            CodeGrader.check_pattern("Hello World", r"NotFound"),
        ]
        result = CodeGrader.check_all(results)
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.5)

    def test_check_any_some_passed(self):
        results = [
            CodeGrader.check_pattern("Hello World", r"NotFound"),
            CodeGrader.check_pattern("Hello World", r"World"),
        ]
        result = CodeGrader.check_any(results)
        self.assertTrue(result.passed)


# ============================================================
# Model Grader Tests - LLM 评估（可选）
# ============================================================

class TestModelGraderAvailability(unittest.TestCase):
    """Test Model Grader availability check."""

    def test_availability_check(self):
        # 这个测试总是通过，只是检查函数是否工作
        available = model_grader_available()
        self.assertIsInstance(available, bool)


@pytest.mark.model_grader
class TestModelGraderSummary(unittest.TestCase):
    """Test summary evaluation (requires LLM API)."""

    def test_good_summary(self):
        """测试高质量摘要"""
        original = """
        用户：你好，我是张三，我是一名软件工程师，我正在开发一个AI Agent项目。
        助手：你好张三！很高兴认识你。AI Agent项目听起来很有趣，能告诉我更多细节吗？
        用户：这个项目使用Python和FastAPI，主要功能是RAG知识库问答。
        助手：了解了，这是一个很实用的项目。Python+FastAPI是很好的技术选择。
        用户：对，我还想加入记忆功能，让Agent能记住用户信息。
        助手：记忆功能确实能提升用户体验。你可以考虑使用数据库存储用户偏好。
        """

        summary = """
        ## 对话摘要
        - 用户张三是一名软件工程师
        - 正在开发AI Agent项目，使用Python+FastAPI
        - 主要功能：RAG知识库问答
        - 计划添加记忆功能存储用户信息
        """

        grader = ModelGrader()
        result = grader.evaluate_summary(original, summary)

        # 这是一个好的摘要，应该得高分
        self.assertGreaterEqual(result.score, 0.7)
        self.assertTrue(result.passed)

    def test_poor_summary(self):
        """测试低质量摘要"""
        original = """
        用户：你好，我是张三，我是一名软件工程师。
        助手：你好！
        用户：我想了解光刻技术的原理。
        助手：光刻是半导体制造的核心工艺，使用光化学反应将图案转移到硅片上。
        用户：具体步骤是什么？
        助手：主要包括：涂胶、曝光、显影、刻蚀四个步骤。
        """

        summary = "用户和助手聊了聊天。"  # 太简略，丢失关键信息

        grader = ModelGrader()
        result = grader.evaluate_summary(original, summary)

        # 这是一个差的摘要，应该得低分
        self.assertLess(result.score, 0.7)


@pytest.mark.model_grader
class TestModelGraderToolSelection(unittest.TestCase):
    """Test tool selection evaluation (requires LLM API)."""

    def test_appropriate_tool_selection(self):
        """测试合理的工具选择"""
        grader = ModelGrader()
        result = grader.evaluate_tool_selection(
            user_query="帮我查一下光刻技术的原理",
            selected_tool="search_knowledge_base",
            available_tools=[
                "search_knowledge_base",
                "remember_fact",
                "list_memories",
                "get_system_config"
            ],
            tool_descriptions={
                "search_knowledge_base": "在知识库中搜索专业知识和文档内容",
                "remember_fact": "记住关于用户的重要信息",
                "list_memories": "列出已记忆的用户事实",
                "get_system_config": "查询系统配置信息"
            }
        )

        # 搜索知识库是正确的选择
        self.assertGreaterEqual(result.score, 0.7)
        self.assertTrue(result.passed)

    def test_inappropriate_tool_selection(self):
        """测试不合理的工具选择"""
        grader = ModelGrader()
        result = grader.evaluate_tool_selection(
            user_query="帮我查一下光刻技术的原理",
            selected_tool="remember_fact",  # 错误选择
            available_tools=[
                "search_knowledge_base",
                "remember_fact",
                "list_memories"
            ],
            tool_descriptions={
                "search_knowledge_base": "在知识库中搜索专业知识和文档内容",
                "remember_fact": "记住关于用户的重要信息",
                "list_memories": "列出已记忆的用户事实"
            }
        )

        # remember_fact 不适合搜索问题
        self.assertLess(result.score, 0.7)


@pytest.mark.model_grader
class TestModelGraderResponse(unittest.TestCase):
    """Test response quality evaluation (requires LLM API)."""

    def test_helpful_response(self):
        """测试有帮助的回答"""
        grader = ModelGrader()
        result = grader.evaluate_response(
            user_query="Python中如何读取JSON文件？",
            response="""
            在Python中读取JSON文件可以使用内置的json模块：

            ```python
            import json

            # 读取JSON文件
            with open('data.json', 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 现在data是一个Python字典或列表
            print(data)
            ```

            关键点：
            1. 使用`json.load()`读取文件对象
            2. 建议指定`encoding='utf-8'`处理中文
            3. 返回的是Python原生数据结构
            """
        )

        self.assertGreaterEqual(result.score, 0.7)
        self.assertTrue(result.passed)


# ============================================================
# 集成测试示例
# ============================================================

class TestGraderIntegration(unittest.TestCase):
    """Integration tests combining Code and Model graders."""

    def test_tool_call_with_code_grader(self):
        """使用 Code Grader 验证工具调用格式"""
        sample_tool_calls = [
            {"name": "search_knowledge_base", "arguments": {"query": "光刻技术原理"}},
            {"name": "remember_fact", "arguments": {"fact": "用户名叫张三", "category": "fact"}},
            {"name": "get_system_config", "arguments": {"config_type": "llm"}}
        ]
        for tool_call in sample_tool_calls:
            # 检查基本结构
            result = CodeGrader.check_json_structure(
                tool_call,
                required_fields=["name", "arguments"]
            )
            self.assertTrue(result.passed, f"Tool call structure invalid: {tool_call}")

    def test_conversation_token_estimation(self):
        """估算对话的 token 数量"""
        sample_conversation = [
            {"role": "user", "content": "你好，我是张三"},
            {"role": "assistant", "content": "你好张三！有什么可以帮助你的吗？"},
            {"role": "user", "content": "我想了解一下光刻技术"},
            {"role": "assistant", "content": "光刻技术是半导体制造的核心工艺..."},
        ]
        total_content = " ".join([
            msg["content"] for msg in sample_conversation
        ])
        tokens = CodeGrader.estimate_tokens(total_content)

        # 应该在合理范围内
        self.assertGreater(tokens, 0)
        self.assertLess(tokens, 10000)  # 不应该太长


if __name__ == "__main__":
    unittest.main()
