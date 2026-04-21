"""Agent Executor - Core agent loop with tool use support."""

import json
import asyncio
from typing import Dict, Any, Optional, List, Callable, AsyncIterator, TYPE_CHECKING
from dataclasses import dataclass, field
from enum import Enum

from ..tools.base import ToolResult
from ..tools.registry import get_registry
from ..tools.executor import ToolExecutor, ExecutionContext
from .multimodal import (
    convert_tool_result_to_message,
    inject_images_into_conversation
)

if TYPE_CHECKING:
    from .compactor import ConversationCompactor


class AgentState(Enum):
    """Agent状态"""
    IDLE = "idle"
    THINKING = "thinking"
    CALLING_TOOL = "calling_tool"
    PROCESSING_RESULT = "processing_result"
    DONE = "done"
    ERROR = "error"


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments
        }


@dataclass
class AgentStep:
    """Agent执行步骤"""
    step_type: str  # "thinking" | "tool_call" | "tool_result" | "response"
    content: Any
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.step_type,
            "content": self.content,
            "metadata": self.metadata
        }


@dataclass
class AgentConfig:
    """Agent配置"""
    max_iterations: int = 0  # 最大循环次数，0=无限制（Claude Code模式）
    enable_reasoning: bool = True  # 是否启用推理展示
    tool_timeout: int = 30  # 工具超时
    parallel_tools: bool = False  # 是否并行执行工具
    provider: str = "openai"  # LLM提供商，用于多模态消息格式选择
    enable_compaction: bool = True  # 是否启用自动上下文压缩


class AgentExecutor:
    """
    Agent执行器 - 核心循环

    流程:
    1. 用户输入 → 模型思考
    2. 模型决定是否调用工具
    3. 如需调用 → 执行工具 → 返回结果 → 回到步骤1
    4. 如不需要 → 返回最终响应
    """

    def __init__(
        self,
        model_adapter,
        config: AgentConfig = None,
        context: ExecutionContext = None,
        compactor: "ConversationCompactor" = None
    ):
        self.model = model_adapter
        self.config = config or AgentConfig()
        self.context = context
        self.compactor = compactor
        self.tool_executor = ToolExecutor()
        self.registry = get_registry()

        # 状态
        self.state = AgentState.IDLE
        self.iteration = 0
        self._steps: List[AgentStep] = []

    async def run(
        self,
        prompt: str,
        messages: List[dict] = None,
        system_prompt: str = None,
        **kwargs
    ) -> AsyncIterator[AgentStep]:
        """
        执行Agent循环（流式）

        Yields:
            AgentStep: 每个执行步骤
        """
        self.state = AgentState.THINKING
        self.iteration = 0
        self._steps = []

        # 准备消息
        conversation = []

        # 始终添加system_prompt（如果有）
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})

        # 添加历史消息
        if messages:
            for msg in messages:
                # 跳过非字典格式的消息
                if not isinstance(msg, dict):
                    continue
                # 跳过旧的system消息，使用新的
                if msg.get("role") != "system":
                    conversation.append(msg)

        # 添加当前用户消息
        conversation.append({"role": "user", "content": prompt})

        # 检查是否需要压缩上下文（Context Compaction）
        if (self.compactor and
            self.config.enable_compaction and
            self.compactor.should_compact(conversation)):

            try:
                result = await self.compactor.compact(conversation, self.model)
                if result.success and result.summary:
                    # 应用压缩
                    conversation = self.compactor.apply_compaction(conversation, result)

                    # 通知调用方发生了压缩
                    step = AgentStep("compaction", result.to_dict())
                    self._steps.append(step)
                    yield step
            except Exception as e:
                # 压缩失败不应阻止对话继续
                import logging
                logging.getLogger(__name__).warning(f"Compaction failed: {e}")

        # 获取可用工具
        tools = self.registry.to_schemas()

        while True:
            # 检查迭代限制（0=无限制，Claude Code模式）
            if self.config.max_iterations > 0 and self.iteration >= self.config.max_iterations:
                self.state = AgentState.ERROR
                yield AgentStep("error", f"Reached maximum iterations ({self.config.max_iterations})")
                return

            self.iteration += 1

            # 调用模型
            self.state = AgentState.THINKING

            try:
                response = await self._call_model(conversation, tools, **kwargs)
            except Exception as e:
                self.state = AgentState.ERROR
                yield AgentStep("error", str(e))
                return

            # 处理响应
            if response.get("tool_calls"):
                # 模型请求调用工具
                tool_calls = response["tool_calls"]

                # yield思考内容（如果有）
                if response.get("reasoning"):
                    step = AgentStep("thinking", response["reasoning"])
                    self._steps.append(step)
                    yield step

                if response.get("content"):
                    step = AgentStep("thinking", response["content"])
                    self._steps.append(step)
                    yield step

                # 执行工具
                self.state = AgentState.CALLING_TOOL
                tool_results = []

                for tc in tool_calls:
                    tool_call = ToolCall(
                        id=tc.get("id", f"call_{self.iteration}"),
                        name=tc["function"]["name"],
                        arguments=json.loads(tc["function"]["arguments"])
                    )

                    # yield工具调用
                    step = AgentStep("tool_call", tool_call.to_dict())
                    self._steps.append(step)
                    yield step

                    # 执行工具
                    result = await self.tool_executor.execute(
                        tool_call.name,
                        tool_call.arguments,
                        self.context
                    )

                    tool_results.append({
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "result": result
                    })

                    # yield工具结果
                    step = AgentStep("tool_result", {
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "result": result.to_dict()
                    })
                    self._steps.append(step)
                    yield step

                # 更新对话历史
                self.state = AgentState.PROCESSING_RESULT

                # 添加assistant消息（带tool_calls）
                assistant_msg = {"role": "assistant", "content": response.get("content", "")}
                if response.get("tool_calls"):
                    assistant_msg["tool_calls"] = response["tool_calls"]
                conversation.append(assistant_msg)

                # 添加工具结果（支持多模态）
                for tr in tool_results:
                    tool_msg = convert_tool_result_to_message(
                        tool_call_id=tr["tool_call_id"],
                        result=tr["result"],
                        provider=self.config.provider
                    )
                    conversation.append(tool_msg)

                # 在调用模型前注入图片到对话（如果有）
                conversation = inject_images_into_conversation(
                    conversation,
                    provider=self.config.provider
                )

                # 继续循环
                continue

            else:
                # 模型返回最终响应
                self.state = AgentState.DONE

                if response.get("reasoning"):
                    step = AgentStep("thinking", response["reasoning"])
                    self._steps.append(step)
                    yield step

                step = AgentStep("response", response.get("content", ""))
                self._steps.append(step)
                yield step
                return

        # 注：迭代限制检查已移至循环内部

    async def run_stream(
        self,
        prompt: str,
        messages: List[dict] = None,
        system_prompt: str = None,
        attached_images: List[dict] = None,
        **kwargs
    ) -> AsyncIterator[dict]:
        """
        流式执行Agent循环

        与run()类似，但使用流式API，实时返回文本片段

        Args:
            prompt: 用户输入
            messages: 历史消息
            system_prompt: 系统提示
            attached_images: 附带的图片列表，每个元素包含 base64 和 media_type

        Yields:
            dict: 流式事件
                - {"type": "reasoning", "text": "..."}
                - {"type": "content", "text": "..."}
                - {"type": "tool_call", "data": {...}}
                - {"type": "tool_result", "data": {...}}
                - {"type": "done"}
                - {"type": "error", "message": "..."}
        """
        self.state = AgentState.THINKING
        self.iteration = 0

        # 准备消息
        conversation = []

        # 始终添加system_prompt（如果有）
        if system_prompt:
            conversation.append({"role": "system", "content": system_prompt})

        # 添加历史消息
        if messages:
            for msg in messages:
                # 跳过非字典格式的消息
                if not isinstance(msg, dict):
                    continue
                # 跳过旧的system消息，使用新的
                if msg.get("role") != "system":
                    conversation.append(msg)

        # 添加当前用户消息（支持多模态图片）
        user_message = self._build_user_message(prompt, attached_images)
        conversation.append(user_message)

        # 获取可用工具
        tools = self.registry.to_schemas()

        while True:
            # 检查迭代限制（0=无限制，Claude Code模式）
            if self.config.max_iterations > 0 and self.iteration >= self.config.max_iterations:
                self.state = AgentState.ERROR
                yield {"type": "error", "message": f"Reached maximum iterations ({self.config.max_iterations})"}
                return

            self.iteration += 1

            self.state = AgentState.THINKING

            try:
                # 流式调用模型
                collected_content = ""
                collected_reasoning = ""
                tool_calls_data = []

                async for chunk in self._stream_model(conversation, tools, **kwargs):
                    chunk_type = chunk.get("type")

                    if chunk_type == "reasoning":
                        collected_reasoning += chunk.get("text", "")
                        yield chunk

                    elif chunk_type == "content":
                        collected_content += chunk.get("text", "")
                        yield chunk

                    elif chunk_type == "tool_calls":
                        tool_calls_data = chunk.get("data", [])

                # 检查是否有工具调用
                if tool_calls_data:
                    self.state = AgentState.CALLING_TOOL
                    tool_results = []

                    for tc in tool_calls_data:
                        tool_call = ToolCall(
                            id=tc.get("id", f"call_{self.iteration}"),
                            name=tc["function"]["name"],
                            arguments=json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                        )

                        # yield工具调用事件
                        yield {
                            "type": "tool_call",
                            "data": tool_call.to_dict()
                        }

                        # 执行工具
                        result = await self.tool_executor.execute(
                            tool_call.name,
                            tool_call.arguments,
                            self.context
                        )

                        tool_results.append({
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "result": result
                        })

                        # yield工具结果事件
                        yield {
                            "type": "tool_result",
                            "data": {
                                "tool_call_id": tool_call.id,
                                "name": tool_call.name,
                                "result": result.to_dict()
                            }
                        }

                    # 更新对话历史
                    self.state = AgentState.PROCESSING_RESULT

                    assistant_msg = {"role": "assistant", "content": collected_content}
                    assistant_msg["tool_calls"] = tool_calls_data
                    conversation.append(assistant_msg)

                    # 添加工具结果（支持多模态）
                    for tr in tool_results:
                        tool_msg = convert_tool_result_to_message(
                            tool_call_id=tr["tool_call_id"],
                            result=tr["result"],
                            provider=self.config.provider
                        )
                        conversation.append(tool_msg)

                    # 在调用模型前注入图片到对话（如果有）
                    conversation = inject_images_into_conversation(
                        conversation,
                        provider=self.config.provider
                    )

                    continue

                else:
                    # 最终响应
                    self.state = AgentState.DONE
                    yield {"type": "done"}
                    return

            except Exception as e:
                self.state = AgentState.ERROR
                yield {"type": "error", "message": str(e)}
                return

        # 注：迭代限制检查已移至循环内部

    async def _call_model(
        self,
        messages: List[dict],
        tools: List[dict],
        **kwargs
    ) -> dict:
        """调用模型（非流式）"""
        # 调用model adapter的chat_with_tools方法
        if hasattr(self.model, 'chat_with_tools'):
            return await self._async_call(
                self.model.chat_with_tools,
                messages=messages,
                tools=tools,
                **kwargs
            )
        else:
            # 回退到普通chat
            result = await self._async_call(
                self.model.chat,
                prompt="",
                messages=messages,
                **kwargs
            )
            return {"content": result}

    async def _stream_model(
        self,
        messages: List[dict],
        tools: List[dict],
        **kwargs
    ) -> AsyncIterator[dict]:
        """流式调用模型"""
        if hasattr(self.model, 'chat_stream_with_tools'):
            # 使用支持工具的流式方法
            gen = self.model.chat_stream_with_tools(
                messages=messages,
                tools=tools,
                **kwargs
            )
            for chunk in gen:
                yield chunk
        else:
            # 回退到普通流式
            gen = self.model.chat_stream(
                prompt="",
                messages=messages,
                **kwargs
            )
            for chunk in gen:
                yield chunk

    async def _async_call(self, func, *args, **kwargs):
        """将同步函数转为异步"""
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    def _build_user_message(self, prompt: str, attached_images: List[dict] = None) -> dict:
        """
        构建用户消息（支持多模态图片）

        Args:
            prompt: 用户文本输入
            attached_images: 附带的图片列表，每个元素包含 base64 和 media_type

        Returns:
            用户消息字典，格式取决于是否有图片
        """
        if not attached_images:
            # 纯文本消息
            return {"role": "user", "content": prompt}

        # 多模态消息：包含文本和图片
        from .multimodal import MultimodalMessageBuilder

        content_parts = []

        # 添加文本部分
        if prompt:
            content_parts.append({"type": "text", "text": prompt})

        # 添加图片部分
        for img in attached_images:
            base64_data = img.get("base64")
            media_type = img.get("media_type", "image/png")
            if base64_data:
                image_content = MultimodalMessageBuilder.build_image_content(
                    base64_data=base64_data,
                    media_type=media_type,
                    provider=self.config.provider
                )
                content_parts.append(image_content)

        return {"role": "user", "content": content_parts}

    @property
    def steps(self) -> List[AgentStep]:
        """获取所有执行步骤"""
        return self._steps.copy()
