"""Memory Manager - 记忆管理器

融合 GPT Bio Tool 和 Claude CLAUDE.md 的设计理念：
- 不使用 RAG，直接注入 system prompt
- 带时间戳的结构化记忆（参考 GPT Bio Tool 格式）
- 用户可控（查看、编辑、删除）
- 自动提取 + 显式记忆

参考:
- ChatGPT Memory: https://embracethered.com/blog/posts/2025/chatgpt-how-does-chat-history-memory-preferences-work/
- Claude Memory: https://skywork.ai/blog/claude-memory-a-deep-dive-into-anthropics-persistent-context-solution/
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..storage.database import Database

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    记忆管理器 - 管理用户事实和对话上下文

    设计原则：
    1. 简单透明 - 用户能理解记忆内容
    2. 高效注入 - 带时间戳，直接注入 system prompt
    3. 分层记忆 - User Facts + Conversation Summary
    4. 自动 + 显式 - 支持自动提取和用户主动记忆

    记忆层次：
    - Layer 1: User Facts (≤50条) - 跨对话的用户偏好和事实
    - Layer 2: Conversation Summary - 当前对话摘要（可选）
    - Layer 3: Project Context - 项目级上下文（未来扩展）
    """

    # 记忆类别
    CATEGORY_PREFERENCE = "preference"  # 用户偏好
    CATEGORY_FACT = "fact"              # 用户事实
    CATEGORY_PROJECT = "project"        # 项目相关
    CATEGORY_GENERAL = "general"        # 通用

    def __init__(self, db: Database):
        """
        初始化记忆管理器

        Args:
            db: Database 实例
        """
        self.db = db

    # ================================================================
    # Layer 1: User Facts
    # ================================================================

    def add_fact(
        self,
        fact: str,
        category: str = "general",
        source: Optional[str] = None,
        confidence: float = 1.0
    ) -> int:
        """
        添加用户事实

        Args:
            fact: 事实内容
            category: 类别 (preference, fact, project, general)
            source: 来源 (conversation_id 或 "manual")
            confidence: 置信度 (0.0-1.0)

        Returns:
            事实 ID

        Note:
            - 自动去重（相同 fact 会更新而非新增）
            - 超过 50 条时自动删除最旧的
        """
        fact = fact.strip()
        if not fact:
            return 0

        # 规范化类别
        if category not in (self.CATEGORY_PREFERENCE, self.CATEGORY_FACT,
                           self.CATEGORY_PROJECT, self.CATEGORY_GENERAL):
            category = self.CATEGORY_GENERAL

        fact_id = self.db.add_user_fact(
            fact=fact,
            category=category,
            source=source,
            confidence=confidence
        )

        logger.info(f"Added fact #{fact_id}: {fact[:50]}...")
        return fact_id

    def get_facts(
        self,
        category: Optional[str] = None,
        limit: int = 20
    ) -> list[dict]:
        """
        获取用户事实列表

        Args:
            category: 可选，按类别过滤
            limit: 最大返回数量

        Returns:
            事实列表 [{id, fact, category, source, confidence, created_at, updated_at}]
        """
        return self.db.get_user_facts(category=category, limit=limit)

    def delete_fact(self, fact_id: int) -> bool:
        """
        删除指定事实

        Args:
            fact_id: 事实 ID

        Returns:
            是否删除成功
        """
        success = self.db.delete_user_fact(fact_id)
        if success:
            logger.info(f"Deleted fact #{fact_id}")
        return success

    def clear_all_facts(self) -> int:
        """
        清空所有事实（谨慎使用）

        Returns:
            删除的数量
        """
        facts = self.get_facts(limit=100)
        count = 0
        for f in facts:
            if self.delete_fact(f["id"]):
                count += 1
        logger.warning(f"Cleared {count} facts")
        return count

    # ================================================================
    # Layer 2: Conversation Summary (简化版)
    # ================================================================

    def get_conversation_summary(self, conv_id: str) -> Optional[str]:
        """
        获取对话摘要

        Args:
            conv_id: 对话 ID

        Returns:
            摘要文本，如果没有则返回 None

        Note:
            当前简化实现，直接返回 None
            完整版需要调用 LLM 生成摘要
        """
        # TODO: P2-5 实现对话摘要
        # 需要：
        # 1. 检查对话长度是否超过阈值
        # 2. 调用 LLM 生成摘要
        # 3. 缓存摘要到数据库
        return None

    # ================================================================
    # 核心：上下文注入
    # ================================================================

    def get_context_injection(
        self,
        conv_id: Optional[str] = None,
        max_facts: int = 20,
        include_summary: bool = True
    ) -> str:
        """
        生成注入 system prompt 的记忆文本

        格式参考 GPT Bio Tool：
        ```
        ## 关于用户
        1. [2026-01-20] 偏好使用中文回复
        2. [2026-01-22] 正在开发 Agent 项目

        ## 当前对话
        - 主题：...
        ```

        Args:
            conv_id: 可选，当前对话 ID（用于获取对话摘要）
            max_facts: 最大事实数量
            include_summary: 是否包含对话摘要

        Returns:
            格式化的记忆文本，如果没有记忆则返回空字符串
        """
        sections = []

        # Section 1: User Facts
        facts = self.get_facts(limit=max_facts)
        if facts:
            lines = ["## 关于用户"]
            for i, f in enumerate(facts, 1):
                # 格式: "1. [2026-01-20] 内容"
                date_str = self._format_date(f.get("created_at"))
                category_tag = self._get_category_tag(f.get("category"))
                lines.append(f"{i}. [{date_str}]{category_tag} {f['fact']}")
            sections.append("\n".join(lines))

        # Section 2: Conversation Summary (如果有)
        if include_summary and conv_id:
            summary = self.get_conversation_summary(conv_id)
            if summary:
                sections.append(f"## 当前对话摘要\n{summary}")

        if not sections:
            return ""

        return "\n\n".join(sections)

    def _format_date(self, date_str: Optional[str]) -> str:
        """格式化日期为 YYYY-MM-DD"""
        if not date_str:
            return datetime.now().strftime("%Y-%m-%d")
        try:
            # 尝试解析 ISO 格式
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            return date_str[:10] if len(date_str) >= 10 else date_str

    def _get_category_tag(self, category: Optional[str]) -> str:
        """获取类别标签"""
        tags = {
            self.CATEGORY_PREFERENCE: " [偏好]",
            self.CATEGORY_PROJECT: " [项目]",
        }
        return tags.get(category, "")

    # ================================================================
    # 工具方法
    # ================================================================

    def has_memories(self) -> bool:
        """检查是否有记忆"""
        facts = self.get_facts(limit=1)
        return len(facts) > 0

    def get_stats(self) -> dict:
        """获取记忆统计"""
        facts = self.get_facts(limit=100)

        # 按类别统计
        by_category = {}
        for f in facts:
            cat = f.get("category", "general")
            by_category[cat] = by_category.get(cat, 0) + 1

        return {
            "total_facts": len(facts),
            "by_category": by_category,
            "max_facts": 50,
        }

    def export_facts(self) -> str:
        """
        导出所有事实为 Markdown 格式（类似 Claude 的 CLAUDE.md）

        Returns:
            Markdown 格式的事实列表
        """
        facts = self.get_facts(limit=100)
        if not facts:
            return "# User Facts\n\n*No facts stored.*"

        lines = ["# User Facts", ""]

        # 按类别分组
        by_category = {}
        for f in facts:
            cat = f.get("category", "general")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(f)

        category_names = {
            self.CATEGORY_PREFERENCE: "Preferences",
            self.CATEGORY_FACT: "Facts",
            self.CATEGORY_PROJECT: "Project",
            self.CATEGORY_GENERAL: "General",
        }

        for cat, items in by_category.items():
            lines.append(f"## {category_names.get(cat, cat.title())}")
            lines.append("")
            for f in items:
                date_str = self._format_date(f.get("created_at"))
                lines.append(f"- [{date_str}] {f['fact']}")
            lines.append("")

        return "\n".join(lines)


# ================================================================
# 便捷函数
# ================================================================

_memory_manager: Optional[MemoryManager] = None


def get_memory_manager(db: Optional[Database] = None) -> MemoryManager:
    """
    获取 MemoryManager 单例

    Args:
        db: Database 实例，首次调用时必须提供

    Returns:
        MemoryManager 实例
    """
    global _memory_manager

    if _memory_manager is None:
        if db is None:
            from ..storage.database import get_database
            db = get_database()
        _memory_manager = MemoryManager(db)

    return _memory_manager


def reset_memory_manager():
    """重置 MemoryManager 单例（用于测试）"""
    global _memory_manager
    _memory_manager = None
