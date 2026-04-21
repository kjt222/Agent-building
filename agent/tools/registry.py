"""Tool Registry - Singleton pattern for global tool registration."""

from typing import Dict, List, Optional
import threading

from .base import Tool, ToolCategory


class ToolRegistry:
    """线程安全的工具注册表（单例模式）"""

    _instance: Optional['ToolRegistry'] = None
    _lock = threading.Lock()
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not ToolRegistry._initialized:
            self._tools: Dict[str, Tool] = {}
            self._tools_lock = threading.Lock()
            ToolRegistry._initialized = True

    def register(self, tool: Tool) -> None:
        """注册工具"""
        with self._tools_lock:
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """注销工具"""
        with self._tools_lock:
            return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Optional[Tool]:
        """获取工具"""
        return self._tools.get(name)

    def list_all(
        self,
        category: ToolCategory = None,
        enabled_only: bool = True
    ) -> List[Tool]:
        """列出工具"""
        tools = list(self._tools.values())
        if category:
            tools = [t for t in tools if t.category == category]
        if enabled_only:
            tools = [t for t in tools if t.enabled]
        return tools

    def list_names(self, enabled_only: bool = True) -> List[str]:
        """列出工具名称"""
        return [t.name for t in self.list_all(enabled_only=enabled_only)]

    def to_schemas(self, enabled_only: bool = True) -> List[dict]:
        """转换为LLM的tools参数格式"""
        return [t.to_schema() for t in self.list_all(enabled_only=enabled_only)]

    def clear(self) -> None:
        """清空所有工具（主要用于测试）"""
        with self._tools_lock:
            self._tools.clear()

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# 全局注册表实例
_registry = None


def get_registry() -> ToolRegistry:
    """获取全局注册表"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry
