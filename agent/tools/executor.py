"""Tool Executor - Handles safe execution of tools."""

import asyncio
import time
from typing import Dict, Any, Optional, Callable, Awaitable
from concurrent.futures import ThreadPoolExecutor

from .base import Tool, ToolResult, PermissionLevel
from .registry import ToolRegistry, get_registry


class ExecutionContext:
    """执行上下文"""

    def __init__(
        self,
        session_id: str,
        mode: str = "confirm",
        permission_callback: Callable[[Tool, dict], Awaitable[bool]] = None
    ):
        self.session_id = session_id
        self.mode = mode  # "auto" | "confirm" | "read_only"
        self._permission_callback = permission_callback

    async def request_permission(self, tool: Tool, arguments: dict) -> bool:
        """请求用户确认"""
        if self.mode == "auto":
            return True
        if self.mode == "read_only":
            return tool.permission == PermissionLevel.AUTO
        if self._permission_callback:
            return await self._permission_callback(tool, arguments)
        # 默认：AUTO权限自动通过，其他需要确认
        return tool.permission == PermissionLevel.AUTO


class ToolExecutor:
    """工具执行器 - 负责安全执行工具"""

    def __init__(self, registry: ToolRegistry = None):
        self.registry = registry or get_registry()
        self._cache: Dict[str, tuple] = {}  # (result, timestamp)
        self._thread_pool = ThreadPoolExecutor(max_workers=4)

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        context: ExecutionContext = None
    ) -> ToolResult:
        """执行工具"""

        tool = self.registry.get(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not found")

        if not tool.enabled:
            return ToolResult(success=False, error=f"Tool '{tool_name}' is disabled")

        # 检查缓存
        cache_key = self._make_cache_key(tool_name, arguments)
        if tool.cache_ttl > 0:
            cached = self._cache.get(cache_key)
            if cached and time.time() - cached[1] < tool.cache_ttl:
                result = cached[0]
                result.metadata["cache_hit"] = True
                return result

        # 权限检查
        if context and tool.permission != PermissionLevel.AUTO:
            if not await context.request_permission(tool, arguments):
                return ToolResult(success=False, error="Permission denied by user")

        # 执行（带超时和重试）
        start_time = time.time()
        last_error = None

        for attempt in range(tool.retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._run_handler(tool, arguments),
                    timeout=tool.timeout
                )

                # 确保返回ToolResult
                if not isinstance(result, ToolResult):
                    result = ToolResult(success=True, data=result)

                result.metadata["execution_time_ms"] = (time.time() - start_time) * 1000
                result.metadata["attempts"] = attempt + 1

                # 缓存结果
                if tool.cache_ttl > 0 and result.success:
                    self._cache[cache_key] = (result, time.time())

                return result

            except asyncio.TimeoutError:
                last_error = f"Tool execution timed out after {tool.timeout}s"
            except Exception as e:
                last_error = str(e)

        return ToolResult(
            success=False,
            error=last_error,
            metadata={"execution_time_ms": (time.time() - start_time) * 1000}
        )

    async def _run_handler(self, tool: Tool, arguments: dict) -> ToolResult:
        """运行工具handler"""
        if asyncio.iscoroutinefunction(tool.handler):
            result = await tool.handler(**arguments)
        else:
            # 同步handler在线程池中执行
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._thread_pool,
                lambda: tool.handler(**arguments)
            )

        # 标准化返回值
        if isinstance(result, ToolResult):
            return result
        elif isinstance(result, dict):
            if "success" in result:
                return ToolResult(**result)
            return ToolResult(success=True, data=result)
        else:
            return ToolResult(success=True, data=result)

    def _make_cache_key(self, tool_name: str, arguments: dict) -> str:
        """生成缓存key"""
        args_str = str(sorted(arguments.items()))
        return f"{tool_name}:{hash(args_str)}"

    def clear_cache(self) -> None:
        """清空缓存"""
        self._cache.clear()

    def shutdown(self) -> None:
        """关闭执行器"""
        self._thread_pool.shutdown(wait=False)
