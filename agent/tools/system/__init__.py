"""System Configuration Tools for Agent.

让 Agent 能够回答关于自身配置的问题：
- get_system_config: 查询系统配置（LLM、嵌入模型、RAG设置等）

工具设计原则 (参考 Anthropic 最佳实践):
- 清晰的使用场景说明
- 具体的错误提示和解决建议
- 参数枚举值说明清晰
"""

from typing import Callable, Optional, List
from ..base import Tool, ToolResult, ToolCategory, PermissionLevel
from ..registry import get_registry


def create_system_tools(
    llm_info_loader: Callable[[], dict],
    config_loader: Callable[[], dict],
    behavior_loader: Callable[[], dict] = None,
) -> List[Tool]:
    """
    Create system configuration tools.

    Args:
        llm_info_loader: Function that returns current LLM info
            {provider, model, temperature, ...}
        config_loader: Function that returns app config
        behavior_loader: Function that returns behavior config (optional)

    Returns:
        工具列表 (1个工具)
    """

    # 有效的配置类型
    VALID_CONFIG_TYPES = ["llm", "embedding", "vision", "rag", "knowledge_bases", "all"]

    def get_system_config(config_type: str = "all") -> ToolResult:
        """
        查询系统配置信息。

        支持查询多种配置类型：LLM、嵌入模型、视觉模型、RAG设置、知识库等。
        """
        try:
            # 验证配置类型
            if config_type not in VALID_CONFIG_TYPES:
                return ToolResult(
                    success=False,
                    error=f"无效的配置类型: '{config_type}'",
                    data={
                        "valid_types": VALID_CONFIG_TYPES,
                        "suggestion": "使用 'all' 获取所有配置概览，或选择具体类型",
                        "examples": {
                            "llm": "查询当前使用的语言模型",
                            "embedding": "查询嵌入模型配置",
                            "rag": "查询RAG检索设置",
                            "all": "获取所有配置概览"
                        }
                    }
                )

            # 加载配置
            llm_info = llm_info_loader()
            app_cfg = config_loader()
            behavior = behavior_loader() if behavior_loader else {}

            if config_type == "llm":
                return ToolResult(
                    success=True,
                    data={
                        "provider": llm_info.get("provider", "unknown"),
                        "model": llm_info.get("model", "unknown"),
                        "temperature": llm_info.get("temperature"),
                        "thinking_enabled": llm_info.get("thinking_enabled", False),
                        "description": "当前对话使用的语言模型配置"
                    }
                )

            elif config_type == "embedding":
                rag_cfg = app_cfg.get("rag", {})
                embedding_cfg = rag_cfg.get("embedding", {})
                return ToolResult(
                    success=True,
                    data={
                        "provider": embedding_cfg.get("provider", llm_info.get("provider", "unknown")),
                        "model": embedding_cfg.get("model", "default"),
                        "dimension": embedding_cfg.get("dimension"),
                        "description": "用于知识库向量化的嵌入模型"
                    }
                )

            elif config_type == "vision":
                vision_cfg = app_cfg.get("vision", {})
                if not vision_cfg:
                    return ToolResult(
                        success=True,
                        data={
                            "configured": False,
                            "note": "未配置专用视觉模型，使用具有视觉能力的 LLM（如果支持）",
                            "suggestion": "如需专用视觉模型，可在配置文件中添加 vision 配置"
                        }
                    )
                return ToolResult(
                    success=True,
                    data={
                        "configured": True,
                        "provider": vision_cfg.get("provider"),
                        "model": vision_cfg.get("model"),
                        "description": "用于图像理解的视觉模型"
                    }
                )

            elif config_type == "rag":
                rag_cfg = app_cfg.get("rag", {})
                return ToolResult(
                    success=True,
                    data={
                        "strategy": rag_cfg.get("strategy", "hybrid"),
                        "top_k": rag_cfg.get("top_k", 5),
                        "score_threshold": rag_cfg.get("score_threshold", 0.35),
                        "context_window": "128k tokens",
                        "context_packing_threshold": "102400 tokens (80%)",
                        "description": "RAG检索增强生成的配置参数"
                    }
                )

            elif config_type == "knowledge_bases":
                kbs = app_cfg.get("knowledge_bases", [])
                active_kbs = app_cfg.get("active_kbs", [])
                if isinstance(active_kbs, str):
                    active_kbs = [active_kbs] if active_kbs else []

                kb_list = [
                    {
                        "name": kb.get("name", ""),
                        "active": kb.get("name", "") in active_kbs,
                        "type": kb.get("type", "unknown")
                    }
                    for kb in kbs
                ]

                result_data = {
                    "knowledge_bases": kb_list,
                    "total_count": len(kbs),
                    "active_count": len([kb for kb in kbs if kb.get("name", "") in active_kbs]),
                    "description": "可用的知识库列表及其状态"
                }

                # 如果没有知识库，提供建议
                if not kbs:
                    result_data["note"] = "暂无配置的知识库"
                    result_data["suggestion"] = "在 Settings -> Knowledge Base 中添加知识库"

                return ToolResult(success=True, data=result_data)

            elif config_type == "all":
                # 概览所有配置
                llm_data = {
                    "provider": llm_info.get("provider", "unknown"),
                    "model": llm_info.get("model", "unknown"),
                }

                rag_cfg = app_cfg.get("rag", {})
                embedding_cfg = rag_cfg.get("embedding", {})
                embedding_data = {
                    "provider": embedding_cfg.get("provider", llm_info.get("provider", "unknown")),
                    "model": embedding_cfg.get("model", "default"),
                }

                vision_cfg = app_cfg.get("vision", {})
                vision_data = {
                    "configured": bool(vision_cfg),
                    "provider": vision_cfg.get("provider") if vision_cfg else None,
                    "model": vision_cfg.get("model") if vision_cfg else None,
                }

                kbs = app_cfg.get("knowledge_bases", [])
                active_kbs = app_cfg.get("active_kbs", [])
                if isinstance(active_kbs, str):
                    active_kbs = [active_kbs] if active_kbs else []

                return ToolResult(
                    success=True,
                    data={
                        "llm": llm_data,
                        "embedding": embedding_data,
                        "vision": vision_data,
                        "rag": {
                            "strategy": rag_cfg.get("strategy", "hybrid"),
                            "top_k": rag_cfg.get("top_k", 5),
                        },
                        "knowledge_bases": {
                            "total": len(kbs),
                            "active": active_kbs,
                        },
                        "description": "系统配置概览（使用 config_type 参数获取详细信息）"
                    }
                )

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"获取配置时发生错误: {str(e)}",
                data={
                    "suggestion": "这可能是配置加载问题，请检查配置文件是否正确"
                }
            )

    # ============================================================
    # 工具描述（遵循 Anthropic 最佳实践）
    # ============================================================
    GET_SYSTEM_CONFIG_DESCRIPTION = """查询当前系统配置信息（模型、嵌入、RAG设置等）。

## 使用场景
- 用户询问"你是什么模型"、"你用的什么AI"
- 用户询问"你的配置是什么"、"你怎么设置的"
- 用户询问"你用什么嵌入模型"
- 用户想了解知识库配置
- 调试或排查问题时需要了解系统配置

## 不适用场景
- 搜索知识库内容 → 使用 search_knowledge_base
- 查看知识库详情 → 使用 list_knowledge_bases
- 记忆用户信息 → 使用 remember_fact

## 配置类型说明
- **llm**: 当前语言模型（provider, model, temperature）
- **embedding**: 嵌入模型（用于知识库向量化）
- **vision**: 视觉模型（图像理解）
- **rag**: RAG检索设置（策略、top_k、阈值）
- **knowledge_bases**: 知识库列表和状态
- **all**: 所有配置概览（默认）

## 示例对话
用户："你是什么模型？"
→ get_system_config(config_type="llm")
→ "我使用的是 OpenAI 的 gpt-4o 模型"

用户："你的RAG设置是什么？"
→ get_system_config(config_type="rag")
→ "使用混合检索策略，top_k=5，相似度阈值0.35"

用户："你的配置"
→ get_system_config(config_type="all")
→ 返回所有配置概览"""

    # 创建工具定义
    tools = [
        Tool(
            name="get_system_config",
            description=GET_SYSTEM_CONFIG_DESCRIPTION,
            category=ToolCategory.SYSTEM,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "config_type": {
                        "type": "string",
                        "enum": VALID_CONFIG_TYPES,
                        "description": "配置类型：llm(语言模型)、embedding(嵌入模型)、vision(视觉模型)、rag(检索设置)、knowledge_bases(知识库)、all(概览)",
                        "default": "all"
                    }
                },
                "required": []
            },
            handler=get_system_config
        )
    ]

    return tools


def register_system_tools(
    llm_info_loader: Callable[[], dict],
    config_loader: Callable[[], dict],
    behavior_loader: Callable[[], dict] = None,
) -> None:
    """Register system tools to global registry.

    Args:
        llm_info_loader: Function that returns current LLM info
        config_loader: Function that returns app config
        behavior_loader: Function that returns behavior config (optional)
    """
    registry = get_registry()
    tools = create_system_tools(llm_info_loader, config_loader, behavior_loader)
    for tool in tools:
        registry.register(tool)
