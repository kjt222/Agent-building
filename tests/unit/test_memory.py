"""Tests for Memory Management (P2-6).

参考 Anthropic Agent 评估方法：
- Code Grader: API 端点测试、数据持久化
- Model Grader: 记忆提取质量（未来扩展）
"""

import asyncio
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import os

from agent.core.memory import MemoryManager, get_memory_manager, reset_memory_manager


class MockDatabase:
    """Mock Database for testing MemoryManager."""

    def __init__(self):
        self.facts = {}
        self.next_id = 1

    def add_user_fact(self, fact: str, category: str, source: str = None, confidence: float = 1.0) -> int:
        fact_id = self.next_id
        self.next_id += 1
        self.facts[fact_id] = {
            "id": fact_id,
            "fact": fact,
            "category": category,
            "source": source,
            "confidence": confidence,
            "created_at": "2026-01-23T12:00:00",
            "updated_at": "2026-01-23T12:00:00"
        }
        return fact_id

    def get_user_facts(self, category: str = None, limit: int = 50) -> list:
        facts = list(self.facts.values())
        if category:
            facts = [f for f in facts if f["category"] == category]
        return facts[:limit]

    def delete_user_fact(self, fact_id: int) -> bool:
        if fact_id in self.facts:
            del self.facts[fact_id]
            return True
        return False


class TestMemoryManager(unittest.TestCase):
    """Test MemoryManager core functionality."""

    def setUp(self):
        self.mock_db = MockDatabase()
        self.manager = MemoryManager(self.mock_db)

    def test_add_fact(self):
        """测试添加事实"""
        fact_id = self.manager.add_fact("User likes Python", category="preference")
        self.assertGreater(fact_id, 0)

    def test_add_empty_fact(self):
        """测试添加空事实（应返回0）"""
        fact_id = self.manager.add_fact("  ")
        self.assertEqual(fact_id, 0)

    def test_get_facts(self):
        """测试获取事实列表"""
        self.manager.add_fact("Fact 1", category="preference")
        self.manager.add_fact("Fact 2", category="fact")
        self.manager.add_fact("Fact 3", category="preference")

        all_facts = self.manager.get_facts()
        self.assertEqual(len(all_facts), 3)

        pref_facts = self.manager.get_facts(category="preference")
        self.assertEqual(len(pref_facts), 2)

    def test_delete_fact(self):
        """测试删除事实"""
        fact_id = self.manager.add_fact("To be deleted", category="general")
        self.assertTrue(self.manager.delete_fact(fact_id))

        # Delete again should fail
        self.assertFalse(self.manager.delete_fact(fact_id))

    def test_has_memories(self):
        """测试检查是否有记忆"""
        self.assertFalse(self.manager.has_memories())

        self.manager.add_fact("A memory", category="general")
        self.assertTrue(self.manager.has_memories())

    def test_get_stats(self):
        """测试获取统计信息"""
        self.manager.add_fact("Pref 1", category="preference")
        self.manager.add_fact("Pref 2", category="preference")
        self.manager.add_fact("Fact 1", category="fact")

        stats = self.manager.get_stats()
        self.assertEqual(stats["total_facts"], 3)
        self.assertEqual(stats["by_category"]["preference"], 2)
        self.assertEqual(stats["by_category"]["fact"], 1)


class TestContextInjection(unittest.TestCase):
    """Test context injection for system prompt."""

    def setUp(self):
        self.mock_db = MockDatabase()
        self.manager = MemoryManager(self.mock_db)

    def test_empty_injection(self):
        """无记忆时返回空字符串"""
        context = self.manager.get_context_injection()
        self.assertEqual(context, "")

    def test_basic_injection_format(self):
        """测试基本注入格式（GPT Bio Tool 格式）"""
        self.manager.add_fact("用户喜欢 Python", category="preference")
        self.manager.add_fact("用户正在开发 Agent 项目", category="project")

        context = self.manager.get_context_injection()

        # Should contain header
        self.assertIn("## 关于用户", context)
        # Should contain numbered facts
        self.assertIn("1.", context)
        self.assertIn("2.", context)
        # Should contain date format [YYYY-MM-DD]
        self.assertIn("[2026", context)

    def test_category_tags(self):
        """测试类别标签"""
        self.manager.add_fact("偏好内容", category="preference")
        self.manager.add_fact("项目内容", category="project")

        context = self.manager.get_context_injection()

        self.assertIn("[偏好]", context)
        self.assertIn("[项目]", context)


class TestExportFacts(unittest.TestCase):
    """Test Markdown export (Claude CLAUDE.md style)."""

    def setUp(self):
        self.mock_db = MockDatabase()
        self.manager = MemoryManager(self.mock_db)

    def test_export_empty(self):
        """导出空记忆"""
        export = self.manager.export_facts()
        self.assertIn("# User Facts", export)
        self.assertIn("No facts stored", export)

    def test_export_with_facts(self):
        """导出有记忆的情况"""
        self.manager.add_fact("Test preference", category="preference")
        self.manager.add_fact("Test fact", category="fact")

        export = self.manager.export_facts()

        self.assertIn("# User Facts", export)
        self.assertIn("## Preferences", export)
        self.assertIn("## Facts", export)
        self.assertIn("Test preference", export)
        self.assertIn("Test fact", export)


class TestMemorySingleton(unittest.TestCase):
    """Test get_memory_manager singleton."""

    def setUp(self):
        reset_memory_manager()

    def tearDown(self):
        reset_memory_manager()

    def test_singleton_pattern(self):
        """测试单例模式"""
        # Note: This requires proper database setup
        # For now, just test reset works
        reset_memory_manager()
        # Would need to mock get_database for full test


class TestMemoryTools(unittest.TestCase):
    """Test memory tools integration."""

    def test_tools_creation(self):
        """测试记忆工具创建 - 3个工具（get_memory_stats 已合并到 list_memories）"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        mock_manager.add_fact.return_value = 1
        mock_manager.get_facts.return_value = []
        mock_manager.delete_fact.return_value = True
        mock_manager.get_stats.return_value = {"total_facts": 0}

        tools = create_memory_tools(mock_manager)

        # Should create 3 tools (get_memory_stats merged into list_memories)
        self.assertEqual(len(tools), 3)

        # Check tool names
        names = [t.name for t in tools]
        self.assertIn("remember_fact", names)
        self.assertIn("list_memories", names)
        self.assertIn("forget_fact", names)
        # get_memory_stats 已合并到 list_memories(include_stats=true)
        self.assertNotIn("get_memory_stats", names)

    def test_remember_fact_tool(self):
        """测试 remember_fact 工具"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        mock_manager.add_fact.return_value = 42

        tools = create_memory_tools(mock_manager)
        remember_tool = next(t for t in tools if t.name == "remember_fact")

        result = remember_tool.handler(fact="User likes coffee", category="preference")

        self.assertTrue(result.success)
        self.assertEqual(result.data["fact_id"], 42)
        mock_manager.add_fact.assert_called_once()

    def test_remember_fact_empty_validation(self):
        """测试 remember_fact 空输入验证"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        tools = create_memory_tools(mock_manager)
        remember_tool = next(t for t in tools if t.name == "remember_fact")

        result = remember_tool.handler(fact="  ", category="preference")

        self.assertFalse(result.success)
        self.assertIn("suggestion", result.data)
        self.assertIn("examples", result.data)

    def test_remember_fact_too_long(self):
        """测试 remember_fact 内容过长"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        tools = create_memory_tools(mock_manager)
        remember_tool = next(t for t in tools if t.name == "remember_fact")

        long_fact = "x" * 600  # 超过500字符限制
        result = remember_tool.handler(fact=long_fact, category="preference")

        self.assertFalse(result.success)
        self.assertIn("current_length", result.data)
        self.assertIn("max_length", result.data)

    def test_list_memories_tool(self):
        """测试 list_memories 工具"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        mock_manager.get_facts.return_value = [
            {"id": 1, "fact": "Test fact", "category": "general", "created_at": "2026-01-23"}
        ]

        tools = create_memory_tools(mock_manager)
        list_tool = next(t for t in tools if t.name == "list_memories")

        result = list_tool.handler()

        self.assertTrue(result.success)
        self.assertEqual(len(result.data["facts"]), 1)

    def test_list_memories_with_stats(self):
        """测试 list_memories 带统计信息（替代原 get_memory_stats）"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        mock_manager.get_facts.return_value = [
            {"id": 1, "fact": "Test fact", "category": "general", "created_at": "2026-01-23"}
        ]
        mock_manager.get_stats.return_value = {
            "total_facts": 5,
            "by_category": {"preference": 2, "fact": 3}
        }

        tools = create_memory_tools(mock_manager)
        list_tool = next(t for t in tools if t.name == "list_memories")

        # 使用 include_stats=True
        result = list_tool.handler(include_stats=True)

        self.assertTrue(result.success)
        self.assertIn("stats", result.data)
        self.assertEqual(result.data["stats"]["total_facts"], 5)

    def test_list_memories_empty_suggestion(self):
        """测试 list_memories 空结果时的建议"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        mock_manager.get_facts.return_value = []

        tools = create_memory_tools(mock_manager)
        list_tool = next(t for t in tools if t.name == "list_memories")

        result = list_tool.handler()

        self.assertTrue(result.success)
        self.assertIn("message", result.data)
        self.assertIn("suggestion", result.data)

    def test_forget_fact_tool(self):
        """测试 forget_fact 工具"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        mock_manager.delete_fact.return_value = True

        tools = create_memory_tools(mock_manager)
        forget_tool = next(t for t in tools if t.name == "forget_fact")

        result = forget_tool.handler(fact_id=1)

        self.assertTrue(result.success)
        mock_manager.delete_fact.assert_called_once_with(1)

    def test_forget_fact_not_found(self):
        """测试 forget_fact ID 不存在"""
        from agent.tools.memory import create_memory_tools

        mock_manager = MagicMock()
        mock_manager.delete_fact.return_value = False
        mock_manager.get_facts.return_value = [
            {"id": 2, "fact": "Other fact"}
        ]

        tools = create_memory_tools(mock_manager)
        forget_tool = next(t for t in tools if t.name == "forget_fact")

        result = forget_tool.handler(fact_id=999)

        self.assertFalse(result.success)
        self.assertIn("suggestion", result.data)
        self.assertIn("available_ids", result.data)


if __name__ == "__main__":
    unittest.main()
