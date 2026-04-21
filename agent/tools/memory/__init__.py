"""Memory Tools - 记忆管理工具

让 Agent 能够主动记忆用户信息：
- remember_fact: 记住重要信息
- list_memories: 列出已记忆的事实（含统计功能）
- forget_fact: 删除某条记忆

工具设计原则 (参考 Anthropic 最佳实践):
- 清晰的使用场景说明
- 具体的错误提示和解决建议
- 功能整合，减少工具数量
"""

from typing import List, Optional
from ..base import Tool, ToolResult
from ...core.memory import MemoryManager


def create_memory_tools(memory_manager: MemoryManager) -> List[Tool]:
    """
    创建记忆相关工具

    Args:
        memory_manager: MemoryManager 实例

    Returns:
        工具列表 (3个工具，原4个合并)
    """

    # ============================================================
    # 工具 1: remember_fact - 记住用户信息
    # ============================================================
    def remember_fact(
        fact: str,
        category: str = "general"
    ) -> ToolResult:
        """
        记住关于用户的重要信息，保存到长期记忆中。

        这些信息会在未来的对话中自动注入到系统提示词，帮助你更好地理解用户。
        """
        # 验证输入
        if not fact or not fact.strip():
            return ToolResult(
                success=False,
                error="事实内容不能为空。请提供一个简洁的陈述句，例如：'用户偏好使用中文回复'",
                data={
                    "suggestion": "提供具体的事实内容",
                    "examples": [
                        "用户偏好简洁的代码风格",
                        "用户正在开发一个 Agent 项目",
                        "用户的技术栈是 Python + FastAPI"
                    ]
                }
            )

        fact = fact.strip()

        # 长度限制
        if len(fact) > 500:
            return ToolResult(
                success=False,
                error=f"事实内容过长（{len(fact)} 字符），请限制在 500 字符以内。",
                data={
                    "current_length": len(fact),
                    "max_length": 500,
                    "suggestion": "尝试将长事实拆分成多个简短的事实分别保存"
                }
            )

        # 规范化类别
        valid_categories = ["preference", "fact", "project", "general"]
        original_category = category
        if category not in valid_categories:
            category = "general"

        try:
            fact_id = memory_manager.add_fact(
                fact=fact,
                category=category,
                source="agent_tool"
            )

            if fact_id:
                result_data = {
                    "message": f"已记住：{fact}",
                    "fact_id": fact_id,
                    "category": category
                }
                # 如果类别被修正，提示用户
                if original_category != category and original_category not in valid_categories:
                    result_data["category_note"] = f"类别 '{original_category}' 无效，已自动使用 'general'"

                return ToolResult(success=True, data=result_data)
            else:
                return ToolResult(
                    success=False,
                    error="保存失败，可能是数据库连接问题。",
                    data={
                        "suggestion": "请稍后重试，或检查数据库状态"
                    }
                )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"保存记忆时发生错误: {str(e)}",
                data={
                    "suggestion": "这可能是临时问题，请稍后重试"
                }
            )

    # ============================================================
    # 工具 2: list_memories - 列出记忆（合并了 get_memory_stats）
    # ============================================================
    def list_memories(
        category: Optional[str] = None,
        limit: int = 20,
        include_stats: bool = False
    ) -> ToolResult:
        """
        列出已记忆的用户事实，可选包含统计信息。

        合并了原 list_memories 和 get_memory_stats 功能。
        """
        try:
            # 限制最大数量
            limit = min(max(1, limit), 50)  # 至少1条，最多50条

            facts = memory_manager.get_facts(category=category, limit=limit)

            # 格式化输出
            formatted_facts = []
            for f in facts:
                formatted_facts.append({
                    "id": f["id"],
                    "fact": f["fact"],
                    "category": f.get("category", "general"),
                    "created_at": f.get("created_at", "")[:10] if f.get("created_at") else "",
                })

            result_data = {
                "facts": formatted_facts,
                "total": len(formatted_facts),
                "filter": category,
                "limit": limit
            }

            # 可选：包含统计信息
            if include_stats:
                try:
                    stats = memory_manager.get_stats()
                    result_data["stats"] = stats
                except Exception:
                    result_data["stats"] = {"error": "无法获取统计信息"}

            # 友好的空结果提示
            if not facts:
                result_data["message"] = "暂无记忆"
                if category:
                    result_data["suggestion"] = f"没有类别为 '{category}' 的记忆。尝试不使用类别过滤，或使用 remember_fact 添加新记忆。"
                else:
                    result_data["suggestion"] = "使用 remember_fact 工具添加用户信息到长期记忆。"

            return ToolResult(success=True, data=result_data)

        except Exception as e:
            return ToolResult(
                success=False,
                error=f"获取记忆列表时发生错误: {str(e)}",
                data={
                    "suggestion": "这可能是数据库连接问题，请稍后重试"
                }
            )

    # ============================================================
    # 工具 3: forget_fact - 删除记忆
    # ============================================================
    def forget_fact(fact_id: int) -> ToolResult:
        """
        删除指定的记忆。

        需要先使用 list_memories 获取记忆 ID。
        """
        # 验证 ID
        if not isinstance(fact_id, int) or fact_id <= 0:
            return ToolResult(
                success=False,
                error=f"无效的记忆 ID: {fact_id}。ID 必须是正整数。",
                data={
                    "suggestion": "先使用 list_memories 查看所有记忆及其 ID"
                }
            )

        try:
            success = memory_manager.delete_fact(fact_id)

            if success:
                return ToolResult(
                    success=True,
                    data={
                        "message": f"已删除记忆 #{fact_id}",
                        "deleted_id": fact_id
                    }
                )
            else:
                # 获取可用的记忆 ID 列表帮助用户
                try:
                    available_facts = memory_manager.get_facts(limit=10)
                    available_ids = [f["id"] for f in available_facts]
                except Exception:
                    available_ids = []

                return ToolResult(
                    success=False,
                    error=f"未找到记忆 #{fact_id}，可能已被删除或 ID 不存在。",
                    data={
                        "requested_id": fact_id,
                        "suggestion": "使用 list_memories 查看当前所有记忆",
                        "available_ids": available_ids[:5] if available_ids else "无法获取"
                    }
                )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"删除记忆时发生错误: {str(e)}",
                data={
                    "suggestion": "这可能是临时问题，请稍后重试"
                }
            )

    # ============================================================
    # 创建工具列表
    # ============================================================
    from ..base import ToolCategory, PermissionLevel

    # 工具描述遵循 Anthropic 最佳实践：
    # - 明确使用场景
    # - 说明不适用场景
    # - 提供具体示例

    REMEMBER_FACT_DESCRIPTION = """记住关于用户的重要信息，保存到长期记忆中。

## 使用场景
- 用户明确说"记住这个"、"帮我记一下"
- 发现用户的重要偏好（如"我喜欢简洁的代码"）
- 用户分享背景信息（如职业、技术栈）
- 用户提到正在进行的项目

## 不适用场景
- 临时性信息（如"今天天气不错"）→ 不需要长期记忆
- 对话中的一次性请求 → 直接响应即可
- 敏感个人信息（密码、密钥等）→ 不应存储

## 类别说明
- preference: 用户偏好（语言、风格、习惯）
- fact: 用户背景（职业、技能、经验）
- project: 项目信息（当前项目、技术选型）
- general: 其他通用信息

## 示例
- ✅ "用户偏好使用中文回复"（preference）
- ✅ "用户是一名后端开发工程师"（fact）
- ✅ "用户正在开发一个 Agent 项目，使用 Python"（project）
- ❌ "用户说今天很忙"（临时信息，不适合存储）"""

    LIST_MEMORIES_DESCRIPTION = """列出已记忆的用户事实，可选包含统计信息。

## 使用场景
- 用户询问"你记得什么"、"你知道我什么"
- 需要确认之前记住的信息
- 删除记忆前查看记忆 ID
- 查看记忆统计（使用 include_stats=true）

## 不适用场景
- 搜索知识库内容 → 使用 search_knowledge_base
- 添加新记忆 → 使用 remember_fact

## 参数说明
- category: 按类别过滤（preference/fact/project/general）
- limit: 返回数量限制（1-50，默认20）
- include_stats: 是否包含统计信息（默认 false）

## 示例用法
- 查看所有记忆：list_memories()
- 只看偏好：list_memories(category="preference")
- 包含统计：list_memories(include_stats=true)"""

    FORGET_FACT_DESCRIPTION = """删除指定的记忆（不可恢复）。

## 使用场景
- 用户要求"忘记这个"、"删除那条记忆"
- 记忆内容已过时或不再准确
- 用户纠正了之前的信息

## 使用步骤
1. 先用 list_memories 查看记忆列表和 ID
2. 确认要删除的记忆 ID
3. 调用 forget_fact(fact_id=ID)

## 注意事项
- 删除操作不可恢复
- 需要用户确认后执行
- 如果 ID 不存在会返回错误

## 示例
用户："删除关于我职业的那条记忆"
1. list_memories(category="fact") → 找到 ID=5 "用户是后端工程师"
2. forget_fact(fact_id=5) → 删除成功"""

    tools = [
        Tool(
            name="remember_fact",
            description=REMEMBER_FACT_DESCRIPTION,
            category=ToolCategory.SYSTEM,
            permission=PermissionLevel.AUTO,  # 自动执行
            handler=remember_fact,
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "要记住的事实，简洁的陈述句（最多500字符）"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["preference", "fact", "project", "general"],
                        "description": "事实类别",
                        "default": "general"
                    }
                },
                "required": ["fact"]
            },
        ),
        Tool(
            name="list_memories",
            description=LIST_MEMORIES_DESCRIPTION,
            category=ToolCategory.SYSTEM,
            permission=PermissionLevel.AUTO,
            handler=list_memories,
            parameters={
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["preference", "fact", "project", "general"],
                        "description": "按类别过滤"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回数量限制（1-50）",
                        "default": 20
                    },
                    "include_stats": {
                        "type": "boolean",
                        "description": "是否包含统计信息（总数、分类统计）",
                        "default": False
                    }
                },
                "required": []
            },
        ),
        Tool(
            name="forget_fact",
            description=FORGET_FACT_DESCRIPTION,
            category=ToolCategory.SYSTEM,
            permission=PermissionLevel.CONFIRM,  # 需要用户确认
            handler=forget_fact,
            parameters={
                "type": "object",
                "properties": {
                    "fact_id": {
                        "type": "integer",
                        "description": "要删除的记忆 ID（通过 list_memories 获取）"
                    }
                },
                "required": ["fact_id"]
            },
        ),
        # 注意：get_memory_stats 已合并到 list_memories(include_stats=true)
    ]

    return tools
