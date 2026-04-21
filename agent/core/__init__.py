"""Agent Core - Core agent loop and execution."""

from .executor import (
    AgentExecutor,
    AgentConfig,
    AgentState,
    AgentStep,
    ToolCall
)

from .multimodal import (
    MultimodalMessageBuilder,
    extract_images_from_tool_result,
    convert_tool_result_to_message,
    inject_images_into_conversation
)

from .memory import (
    MemoryManager,
    get_memory_manager,
    reset_memory_manager
)

from .compactor import (
    ConversationCompactor,
    CompactionConfig,
    CompactionResult,
    create_compactor
)

__all__ = [
    "AgentExecutor",
    "AgentConfig",
    "AgentState",
    "AgentStep",
    "ToolCall",
    "MultimodalMessageBuilder",
    "extract_images_from_tool_result",
    "convert_tool_result_to_message",
    "inject_images_into_conversation",
    "MemoryManager",
    "get_memory_manager",
    "reset_memory_manager",
    "ConversationCompactor",
    "CompactionConfig",
    "CompactionResult",
    "create_compactor"
]
