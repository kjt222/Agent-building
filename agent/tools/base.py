"""Tool base classes and types."""

from dataclasses import dataclass, field
from typing import Callable, Any, Optional, Union
from enum import Enum


class ToolCategory(Enum):
    """工具分类"""
    KNOWLEDGE = "knowledge"      # 知识检索
    FILE_SYSTEM = "file_system"  # 文件操作
    CODE = "code"                # 代码执行
    WEB = "web"                  # 网络请求
    SYSTEM = "system"            # 系统信息


class PermissionLevel(Enum):
    """权限级别"""
    AUTO = "auto"           # 自动执行，无需确认
    CONFIRM = "confirm"     # 需要用户确认
    DANGEROUS = "dangerous" # 危险操作，需要特别确认


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata
        }

    def __str__(self) -> str:
        if self.success:
            return str(self.data)
        return f"Error: {self.error}"


@dataclass
class Tool:
    """工具定义"""
    name: str
    description: str
    category: ToolCategory
    permission: PermissionLevel
    parameters: dict  # JSON Schema
    handler: Callable[..., Union[ToolResult, dict, str]]

    # 可选配置
    timeout: int = 30
    retries: int = 0
    cache_ttl: int = 0
    enabled: bool = True

    def to_schema(self) -> dict:
        """转换为OpenAI/智谱的function schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, Tool):
            return self.name == other.name
        return False
