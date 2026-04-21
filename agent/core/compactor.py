"""Context Compaction - 对话压缩器

参考 Claude Code 的 Context Compaction 机制：
- 当对话达到 token 阈值时，自动生成摘要
- 摘要替换完整历史，实现"无限上下文"
- 保护最近的消息，确保连贯性

参考来源:
- https://stevekinney.com/courses/ai-development/claude-code-compaction
- https://platform.claude.com/cookbook/tool-use-automatic-context-compaction
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass  # 避免循环导入

logger = logging.getLogger(__name__)


# ================================================================
# 配置
# ================================================================

@dataclass
class CompactionConfig:
    """压缩配置"""

    # 是否启用自动压缩
    enabled: bool = True

    # 触发阈值（token 数）
    # Claude Code 默认 100k，我们也用这个
    token_threshold: int = 100_000

    # 触发比例：当 tokens > threshold * trigger_ratio 时触发
    # 0.75 = 75% 时触发（比 Claude 的 95% 更保守，给完成任务留空间）
    trigger_ratio: float = 0.75

    # 保护最近的消息数量（不会被压缩）
    protected_recent_messages: int = 10

    # 保护最近的 token 数量（与消息数二选一，取较大保护范围）
    protected_recent_tokens: int = 20_000

    # 摘要模型（None = 使用主模型，可指定便宜模型如 gpt-4o-mini）
    summary_model: Optional[str] = None

    # 摘要最大 token
    summary_max_tokens: int = 2000


@dataclass
class CompactionResult:
    """压缩结果"""

    success: bool
    summary: str = ""
    original_tokens: int = 0
    compacted_tokens: int = 0
    original_messages: int = 0
    compacted_messages: int = 0
    protected_messages: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "summary": self.summary,
            "original_tokens": self.original_tokens,
            "compacted_tokens": self.compacted_tokens,
            "original_messages": self.original_messages,
            "compacted_messages": self.compacted_messages,
            "protected_messages": self.protected_messages,
            "timestamp": self.timestamp,
            "error": self.error,
        }


# ================================================================
# 摘要 Prompt（参考 Claude Code）
# ================================================================

SUMMARY_SYSTEM_PROMPT = """你是一个对话摘要助手。你的任务是将长对话压缩成简洁的摘要，保留关键信息。"""

SUMMARY_USER_PROMPT = """请总结以下对话历史，生成一个简洁但完整的摘要。

摘要必须包含以下部分：

## 已完成的工作
列出已经完成的主要任务、创建/修改的文件、解决的问题。

## 当前状态
描述正在进行的工作、当前的进度。

## 关键决策
记录重要的决定、选择的方案、原因。

## 关键信息
保留重要的：
- 文件名和路径
- 代码片段（如果关键）
- 配置值
- 错误信息和解决方案

## 下一步
列出待完成的任务。

格式要求：
- 使用 Markdown 格式
- 简洁明了，避免冗余
- 保留足够细节以便继续工作

---

对话历史：

{conversation}

---

请生成摘要："""


# ================================================================
# 核心类
# ================================================================

class ConversationCompactor:
    """
    对话压缩器 - 实现 Context Compaction

    核心功能：
    1. estimate_tokens(): 估算对话的 token 数量
    2. should_compact(): 判断是否需要压缩
    3. compact(): 执行压缩，生成摘要
    4. apply_compaction(): 应用压缩结果

    使用示例：
    ```python
    compactor = ConversationCompactor(CompactionConfig(
        token_threshold=100_000,
        trigger_ratio=0.75,
    ))

    if compactor.should_compact(messages):
        result = await compactor.compact(messages, llm)
        if result.success:
            messages = compactor.apply_compaction(messages, result)
    ```
    """

    def __init__(self, config: CompactionConfig = None):
        self.config = config or CompactionConfig()
        self._last_result: Optional[CompactionResult] = None

    # ================================================================
    # Token 估算
    # ================================================================

    def estimate_tokens(self, messages: List[Dict[str, Any]]) -> int:
        """
        估算消息列表的 token 数量

        策略：
        1. 优先使用 tiktoken（如果可用）
        2. 否则用简单估算：混合文本约 2.5-3 字符/token

        Args:
            messages: 消息列表

        Returns:
            估算的 token 数量
        """
        total_chars = 0

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # 多模态消息
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text":
                            total_chars += len(item.get("text", ""))
                        elif item.get("type") == "image_url":
                            # 图片估算 ~85 tokens (低分辨率)
                            total_chars += 250

            # 加上 role 和结构开销
            total_chars += len(msg.get("role", "")) + 10

            # tool_calls 额外开销
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    total_chars += len(str(tc.get("function", {}).get("arguments", "")))
                    total_chars += 50  # 结构开销

        # 保守估算：2.5 字符/token（混合中英文）
        # 这比实际略高，确保不会低估
        estimated_tokens = int(total_chars / 2.5)

        return estimated_tokens

    def estimate_tokens_for_text(self, text: str) -> int:
        """估算单个文本的 token 数量"""
        return int(len(text) / 2.5)

    # ================================================================
    # 压缩判断
    # ================================================================

    def should_compact(self, messages: List[Dict[str, Any]]) -> bool:
        """
        判断是否需要压缩

        触发条件：
        tokens > threshold * trigger_ratio

        Args:
            messages: 消息列表

        Returns:
            是否需要压缩
        """
        if not self.config.enabled:
            return False

        if not messages:
            return False

        # 估算 token
        current_tokens = self.estimate_tokens(messages)
        trigger_threshold = int(self.config.token_threshold * self.config.trigger_ratio)

        should = current_tokens > trigger_threshold

        if should:
            logger.info(
                f"Compaction needed: {current_tokens} tokens > {trigger_threshold} threshold "
                f"({self.config.trigger_ratio:.0%} of {self.config.token_threshold})"
            )

        return should

    def get_compaction_status(self, messages: List[Dict[str, Any]]) -> dict:
        """
        获取压缩状态信息

        Returns:
            {current_tokens, threshold, trigger_threshold, ratio, should_compact}
        """
        current_tokens = self.estimate_tokens(messages)
        trigger_threshold = int(self.config.token_threshold * self.config.trigger_ratio)

        return {
            "current_tokens": current_tokens,
            "threshold": self.config.token_threshold,
            "trigger_threshold": trigger_threshold,
            "ratio": current_tokens / self.config.token_threshold if self.config.token_threshold else 0,
            "should_compact": current_tokens > trigger_threshold,
        }

    # ================================================================
    # 执行压缩
    # ================================================================

    async def compact(
        self,
        messages: List[Dict[str, Any]],
        llm,
        context_hint: str = None
    ) -> CompactionResult:
        """
        执行压缩

        流程：
        1. 分离 system 消息和受保护的最近消息
        2. 将需要压缩的消息格式化为文本
        3. 调用 LLM 生成摘要
        4. 返回压缩结果

        Args:
            messages: 完整消息列表
            llm: 模型适配器（需要有 chat() 方法）
            context_hint: 可选的上下文提示（帮助生成更好的摘要）

        Returns:
            CompactionResult
        """
        original_tokens = self.estimate_tokens(messages)
        original_messages = len(messages)

        try:
            # 分离消息
            system_msg, to_compact, protected = self._split_messages(messages)

            if not to_compact:
                logger.info("No messages to compact")
                return CompactionResult(
                    success=True,
                    summary="",
                    original_tokens=original_tokens,
                    compacted_tokens=original_tokens,
                    original_messages=original_messages,
                    compacted_messages=original_messages,
                    protected_messages=len(protected),
                )

            # 格式化需要压缩的消息
            conversation_text = self._format_messages_for_summary(to_compact)

            # 构建摘要 prompt
            summary_prompt = SUMMARY_USER_PROMPT.format(conversation=conversation_text)
            if context_hint:
                summary_prompt = f"上下文提示：{context_hint}\n\n{summary_prompt}"

            # 调用 LLM 生成摘要
            logger.info(f"Generating summary for {len(to_compact)} messages...")

            summary_messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": summary_prompt},
            ]

            # 使用指定的摘要模型或主模型
            summary_kwargs = {"max_tokens": self.config.summary_max_tokens}
            if self.config.summary_model:
                summary_kwargs["model"] = self.config.summary_model

            response = await llm.chat(
                messages=summary_messages,
                **summary_kwargs
            )

            summary = response.get("content", "").strip()

            if not summary:
                raise ValueError("Empty summary generated")

            # 计算压缩后的 token
            compacted_messages_list = self._build_compacted_messages(
                system_msg, summary, protected
            )
            compacted_tokens = self.estimate_tokens(compacted_messages_list)

            result = CompactionResult(
                success=True,
                summary=summary,
                original_tokens=original_tokens,
                compacted_tokens=compacted_tokens,
                original_messages=original_messages,
                compacted_messages=len(compacted_messages_list),
                protected_messages=len(protected),
            )

            self._last_result = result

            logger.info(
                f"Compaction complete: {original_tokens} → {compacted_tokens} tokens "
                f"({(1 - compacted_tokens/original_tokens)*100:.1f}% reduction), "
                f"{original_messages} → {len(compacted_messages_list)} messages"
            )

            return result

        except Exception as e:
            logger.error(f"Compaction failed: {e}")
            return CompactionResult(
                success=False,
                original_tokens=original_tokens,
                original_messages=original_messages,
                error=str(e),
            )

    def _split_messages(
        self,
        messages: List[Dict[str, Any]]
    ) -> tuple[Optional[Dict], List[Dict], List[Dict]]:
        """
        分离消息

        Returns:
            (system_message, to_compact, protected_recent)
        """
        system_msg = None
        other_messages = []

        # 分离 system 消息
        for msg in messages:
            if msg.get("role") == "system":
                system_msg = msg
            else:
                other_messages.append(msg)

        if not other_messages:
            return system_msg, [], []

        # 计算保护范围
        # 方法1：按消息数量
        protected_count = min(
            self.config.protected_recent_messages,
            len(other_messages)
        )

        # 方法2：按 token 数量（从后往前累加）
        protected_by_tokens = 0
        token_sum = 0
        for i in range(len(other_messages) - 1, -1, -1):
            msg_tokens = self.estimate_tokens([other_messages[i]])
            if token_sum + msg_tokens > self.config.protected_recent_tokens:
                break
            token_sum += msg_tokens
            protected_by_tokens += 1

        # 取较大保护范围
        final_protected = max(protected_count, protected_by_tokens)

        # 分割
        split_point = len(other_messages) - final_protected
        to_compact = other_messages[:split_point]
        protected = other_messages[split_point:]

        logger.debug(
            f"Split messages: {len(to_compact)} to compact, "
            f"{len(protected)} protected"
        )

        return system_msg, to_compact, protected

    def _format_messages_for_summary(self, messages: List[Dict[str, Any]]) -> str:
        """将消息格式化为文本，用于生成摘要"""
        lines = []

        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # 处理多模态内容
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                content = "\n".join(text_parts)

            # 格式化角色名
            role_name = {
                "user": "用户",
                "assistant": "助手",
                "tool": "工具",
            }.get(role, role)

            # 处理工具调用
            if msg.get("tool_calls"):
                tool_info = []
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    tool_info.append(f"  - {func.get('name', 'unknown')}({func.get('arguments', '')})")
                if tool_info:
                    content = f"{content}\n[调用工具]\n" + "\n".join(tool_info)

            # 处理工具结果
            if role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                content = f"[工具结果 {tool_call_id}]\n{content}"

            # 截断过长内容
            if len(content) > 2000:
                content = content[:2000] + "...[截断]"

            lines.append(f"**{role_name}**: {content}")

        return "\n\n".join(lines)

    def _build_compacted_messages(
        self,
        system_msg: Optional[Dict],
        summary: str,
        protected: List[Dict]
    ) -> List[Dict[str, Any]]:
        """构建压缩后的消息列表"""
        result = []

        # 添加 system 消息
        if system_msg:
            result.append(system_msg)

        # 添加摘要作为 user 消息（让模型知道之前发生了什么）
        summary_message = {
            "role": "user",
            "content": f"[对话历史摘要]\n\n{summary}\n\n---\n\n请基于以上摘要继续对话。"
        }
        result.append(summary_message)

        # 添加受保护的最近消息
        result.extend(protected)

        return result

    # ================================================================
    # 应用压缩
    # ================================================================

    def apply_compaction(
        self,
        messages: List[Dict[str, Any]],
        result: CompactionResult
    ) -> List[Dict[str, Any]]:
        """
        应用压缩结果，返回新的消息列表

        Args:
            messages: 原始消息列表
            result: 压缩结果

        Returns:
            压缩后的消息列表
        """
        if not result.success or not result.summary:
            return messages

        system_msg, _, protected = self._split_messages(messages)
        return self._build_compacted_messages(system_msg, result.summary, protected)

    # ================================================================
    # 辅助方法
    # ================================================================

    @property
    def last_result(self) -> Optional[CompactionResult]:
        """获取最近一次压缩结果"""
        return self._last_result

    def reset(self):
        """重置状态"""
        self._last_result = None


# ================================================================
# 便捷函数
# ================================================================

def create_compactor(
    enabled: bool = True,
    token_threshold: int = 100_000,
    trigger_ratio: float = 0.75,
    summary_model: str = None,
) -> ConversationCompactor:
    """
    创建压缩器的便捷函数

    Args:
        enabled: 是否启用
        token_threshold: token 阈值
        trigger_ratio: 触发比例
        summary_model: 摘要模型

    Returns:
        ConversationCompactor 实例
    """
    config = CompactionConfig(
        enabled=enabled,
        token_threshold=token_threshold,
        trigger_ratio=trigger_ratio,
        summary_model=summary_model,
    )
    return ConversationCompactor(config)
