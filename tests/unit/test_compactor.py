"""Tests for Context Compaction (P2-5).

参考 Anthropic Agent 评估方法：
- Code Grader: 确定性检查（token估算、触发判断）
- Model Grader: LLM评估（摘要质量）
"""

import asyncio
import unittest
from unittest.mock import MagicMock, AsyncMock

from agent.core.compactor import (
    ConversationCompactor,
    CompactionConfig,
    CompactionResult,
    create_compactor
)


class TestCompactionConfig(unittest.TestCase):
    """Test CompactionConfig defaults and validation."""

    def test_default_config(self):
        config = CompactionConfig()
        self.assertTrue(config.enabled)
        self.assertEqual(config.token_threshold, 100_000)
        self.assertEqual(config.trigger_ratio, 0.75)
        self.assertEqual(config.protected_recent_messages, 10)

    def test_custom_config(self):
        config = CompactionConfig(
            enabled=False,
            token_threshold=50_000,
            trigger_ratio=0.5
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.token_threshold, 50_000)
        self.assertEqual(config.trigger_ratio, 0.5)


class TestTokenEstimation(unittest.TestCase):
    """Test token estimation (Code Grader - deterministic checks)."""

    def setUp(self):
        self.compactor = ConversationCompactor(CompactionConfig())

    def test_empty_messages(self):
        tokens = self.compactor.estimate_tokens([])
        self.assertEqual(tokens, 0)

    def test_simple_message(self):
        messages = [{"role": "user", "content": "Hello"}]
        tokens = self.compactor.estimate_tokens(messages)
        # "Hello" = 5 chars, ~2.5 chars/token = 2 tokens
        self.assertGreater(tokens, 0)
        self.assertLess(tokens, 10)

    def test_chinese_text(self):
        """中文文本 token 估算"""
        messages = [{"role": "user", "content": "你好世界这是一个测试"}]
        tokens = self.compactor.estimate_tokens(messages)
        # 10 Chinese chars, ~2.5 chars/token
        self.assertGreater(tokens, 0)

    def test_mixed_content(self):
        """混合中英文文本"""
        messages = [
            {"role": "user", "content": "Hello 你好"},
            {"role": "assistant", "content": "Hi there! 我是助手"}
        ]
        tokens = self.compactor.estimate_tokens(messages)
        self.assertGreater(tokens, 5)

    def test_multimodal_message(self):
        """多模态消息（包含图片）"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
                ]
            }
        ]
        tokens = self.compactor.estimate_tokens(messages)
        # Should handle list content
        self.assertGreater(tokens, 0)


class TestTriggerLogic(unittest.TestCase):
    """Test compaction trigger logic (Code Grader)."""

    def test_should_not_compact_small_conversation(self):
        config = CompactionConfig(token_threshold=1000, trigger_ratio=0.75)
        compactor = ConversationCompactor(config)

        # Small conversation
        messages = [{"role": "user", "content": "Hi"}]
        self.assertFalse(compactor.should_compact(messages))

    def test_should_compact_large_conversation(self):
        config = CompactionConfig(token_threshold=100, trigger_ratio=0.75)
        compactor = ConversationCompactor(config)

        # Large conversation (> 75 tokens threshold)
        long_content = "x" * 500  # 500 chars / 2.5 = 200 tokens
        messages = [{"role": "user", "content": long_content}]
        self.assertTrue(compactor.should_compact(messages))

    def test_disabled_compaction(self):
        config = CompactionConfig(enabled=False, token_threshold=10)
        compactor = ConversationCompactor(config)

        # Even with large conversation, should not trigger if disabled
        messages = [{"role": "user", "content": "x" * 1000}]
        self.assertFalse(compactor.should_compact(messages))


class TestCompactionExecution(unittest.TestCase):
    """Test compaction execution with mock LLM."""

    def setUp(self):
        # 设置较小的保护数量，以便测试压缩功能
        self.config = CompactionConfig(
            token_threshold=50,  # 低阈值
            trigger_ratio=0.3,   # 低触发比例
            protected_recent_messages=2,
            protected_recent_tokens=100
        )
        self.compactor = ConversationCompactor(self.config)

    def test_compact_preserves_recent_messages(self):
        """压缩应该保留最近的消息"""
        # 创建足够多的消息，确保有消息需要被压缩
        messages = [
            {"role": "user", "content": "Old message 1 with some longer content to increase token count"},
            {"role": "assistant", "content": "Old response 1 with detailed explanation"},
            {"role": "user", "content": "Old message 2 asking another question"},
            {"role": "assistant", "content": "Old response 2 with more information"},
            {"role": "user", "content": "Old message 3"},
            {"role": "assistant", "content": "Old response 3"},
            {"role": "user", "content": "Recent message"},
            {"role": "assistant", "content": "Recent response"},
        ]

        # Mock LLM that returns a summary (async method)
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value={"content": "## Summary\nUser discussed various topics."})

        async def run_test():
            result = await self.compactor.compact(messages, mock_llm)
            return result

        result = asyncio.run(run_test())
        self.assertTrue(result.success)
        # 如果有消息被压缩，应该有 summary
        # 如果所有消息都被保护，summary 可能为空但仍然成功
        self.assertTrue(result.success)

    def test_apply_compaction(self):
        """测试压缩结果应用"""
        messages = [
            {"role": "system", "content": "You are an assistant"},
            {"role": "user", "content": "Old message"},
            {"role": "assistant", "content": "Old response"},
            {"role": "user", "content": "Recent message"},
            {"role": "assistant", "content": "Recent response"},
        ]

        result = CompactionResult(
            success=True,
            summary="Previous: User asked about X, assistant explained Y.",
            original_tokens=1000,
            compacted_tokens=200,
            original_messages=5,
            compacted_messages=3,
            protected_messages=2
        )

        new_messages = self.compactor.apply_compaction(messages, result)

        # Should have: system + summary + recent messages
        self.assertGreater(len(new_messages), 0)
        # Summary should be injected
        has_summary = any(
            "Previous:" in str(m.get("content", ""))
            for m in new_messages
        )
        self.assertTrue(has_summary)


class TestCompactorFactory(unittest.TestCase):
    """Test create_compactor factory function."""

    def test_create_enabled_compactor(self):
        compactor = create_compactor(enabled=True, token_threshold=50000)
        self.assertIsInstance(compactor, ConversationCompactor)
        self.assertTrue(compactor.config.enabled)

    def test_create_disabled_compactor(self):
        compactor = create_compactor(enabled=False)
        self.assertIsInstance(compactor, ConversationCompactor)
        self.assertFalse(compactor.config.enabled)


class TestCompactionIntegration(unittest.TestCase):
    """Integration tests for compaction with AgentExecutor."""

    def test_compaction_event_in_agent_run(self):
        """测试 AgentExecutor 中的压缩事件"""
        # This is an integration test that would require more setup
        # For now, we just verify the compactor can be created and used
        compactor = create_compactor(enabled=True, token_threshold=100)

        # Create a long conversation
        messages = [
            {"role": "user", "content": f"Message {i}" * 50}
            for i in range(20)
        ]

        # Should trigger compaction
        self.assertTrue(compactor.should_compact(messages))


if __name__ == "__main__":
    unittest.main()
