"""Knowledge Base Tools for Agent.

工具设计原则（参考 Anthropic Writing Tools for Agents）：
- 清晰描述：说明何时使用、何时不使用
- 可操作错误：错误消息包含解决建议
- 整合功能：减少工具数量，降低选择难度
"""

from typing import Callable, Optional, List
from ..base import Tool, ToolResult, ToolCategory, PermissionLevel
from ..registry import get_registry


def create_kb_tools(
    config_loader: Callable[[], dict],
    rag_service_builder: Callable[[str], object] = None,
    knowledge_manager: object = None,
) -> List[Tool]:
    """
    创建知识库工具集

    Args:
        config_loader: 加载app配置的函数，返回dict
        rag_service_builder: 构建RAG服务的函数，接收kb_name
        knowledge_manager: KnowledgeManager 实例（推荐）
    """

    def list_knowledge_bases(kb_name: str = None, include_stats: bool = False) -> ToolResult:
        """
        列出知识库或获取指定知识库详情

        合并了原 list_knowledge_bases 和 get_kb_info 功能
        """
        try:
            config = config_loader()
            kbs = config.get("knowledge_bases", [])
            active_kbs = config.get("active_kbs", [])
            if isinstance(active_kbs, str):
                active_kbs = [active_kbs] if active_kbs else []

            # 如果指定了 kb_name，返回该知识库详情
            if kb_name:
                kb_entry = next((kb for kb in kbs if kb.get("name") == kb_name), None)
                if not kb_entry:
                    available = [kb.get("name", "") for kb in kbs]
                    return ToolResult(
                        success=False,
                        error=f"知识库 '{kb_name}' 不存在。可用的知识库：{available}",
                        data={"suggestion": "list_knowledge_bases", "available_kbs": available}
                    )

                from pathlib import Path
                kb_path = Path(kb_entry.get("path", ""))

                # 统计文件信息
                file_count = 0
                total_size = 0
                extensions = {}

                if kb_path.exists():
                    for f in kb_path.rglob("*"):
                        if f.is_file():
                            file_count += 1
                            total_size += f.stat().st_size
                            ext = f.suffix.lower() or "(no extension)"
                            extensions[ext] = extensions.get(ext, 0) + 1

                result_data = {
                    "name": kb_name,
                    "path": str(kb_path),
                    "exists": kb_path.exists(),
                    "active": kb_name in active_kbs,
                    "file_count": file_count,
                    "total_size_mb": round(total_size / (1024 * 1024), 2),
                    "file_types": extensions
                }

                # 如果有 KnowledgeManager，添加策略信息
                if knowledge_manager:
                    try:
                        km_info = knowledge_manager.get_kb_info(kb_name)
                        result_data.update({
                            "indexed_files": km_info.get("file_count", 0),
                            "total_tokens": km_info.get("total_tokens", 0),
                            "retrieval_strategy": knowledge_manager.retrieval_strategy([kb_name]),
                            "threshold_tokens": km_info.get("threshold", 102400),
                        })
                    except Exception:
                        pass

                return ToolResult(success=True, data=result_data)

            # 列出所有知识库
            kb_list = []
            for kb in kbs:
                kb_info = {
                    "name": kb.get("name", ""),
                    "path": kb.get("path", ""),
                    "active": kb.get("name", "") in active_kbs
                }

                # 如果需要统计信息
                if include_stats:
                    from pathlib import Path
                    kb_path = Path(kb.get("path", ""))
                    if kb_path.exists():
                        file_count = sum(1 for f in kb_path.rglob("*") if f.is_file())
                        kb_info["file_count"] = file_count

                kb_list.append(kb_info)

            result = {
                "knowledge_bases": kb_list,
                "total_count": len(kbs),
                "active_count": len([kb for kb in kbs if kb.get("name", "") in active_kbs]),
                "active_kbs": active_kbs
            }
            return ToolResult(success=True, data=result)

        except Exception as e:
            return ToolResult(success=False, error=f"获取知识库信息失败：{str(e)}")

    def search_knowledge_base(query: str, kb_name: str = None) -> ToolResult:
        """在知识库中搜索专业知识和文档内容"""
        try:
            config = config_loader()
            active_kbs = config.get("active_kbs", [])
            if isinstance(active_kbs, str):
                active_kbs = [active_kbs] if active_kbs else []

            if not active_kbs:
                all_kbs = [kb.get("name", "") for kb in config.get("knowledge_bases", [])]
                return ToolResult(
                    success=False,
                    error="没有激活的知识库。请先在设置中激活知识库，或使用 list_knowledge_bases 查看可用知识库。",
                    data={
                        "suggestion": "list_knowledge_bases",
                        "available_kbs": all_kbs,
                        "help": "在 Settings -> Knowledge Base 中激活需要的知识库"
                    }
                )

            # 如果指定了kb_name，检查是否激活
            if kb_name:
                if kb_name not in active_kbs:
                    return ToolResult(
                        success=False,
                        error=f"知识库 '{kb_name}' 未激活。当前激活的知识库：{active_kbs}",
                        data={
                            "suggestion": f"在设置中激活 '{kb_name}'，或搜索已激活的知识库",
                            "active_kbs": active_kbs
                        }
                    )
                target_kbs = [kb_name]
            else:
                target_kbs = active_kbs

            # 优先使用 KnowledgeManager（混合策略）
            if knowledge_manager:
                try:
                    strategy = knowledge_manager.retrieval_strategy(target_kbs)

                    context = knowledge_manager.get_context(query, target_kbs)
                    search_results = knowledge_manager.search(query, kb_names=target_kbs, limit=10)

                    if not context and not search_results:
                        return ToolResult(
                            success=True,
                            data={
                                "query": query,
                                "strategy": strategy,
                                "context": "",
                                "search_results": [],
                                "count": 0,
                                "searched_kbs": target_kbs,
                                "note": "未找到相关内容。建议：1) 尝试不同的关键词 2) 检查知识库是否包含相关文档"
                            }
                        )

                    return ToolResult(
                        success=True,
                        data={
                            "query": query,
                            "strategy": strategy,
                            "context": context[:2000] if context else "",
                            "context_length": len(context) if context else 0,
                            "search_results": [
                                {
                                    "kb_name": r.get("kb_name", ""),
                                    "filename": r.get("filename", ""),
                                    "snippet": r.get("snippet", "")[:300],
                                }
                                for r in search_results
                            ],
                            "count": len(search_results),
                            "searched_kbs": target_kbs
                        }
                    )
                except Exception as e:
                    pass  # 回退到旧的 RAG

            # 回退：使用旧的 RAG service
            if not rag_service_builder:
                return ToolResult(
                    success=False,
                    error="检索服务未配置。请检查 RAG 配置是否正确。",
                    data={"help": "确保 app.yaml 中配置了 rag 部分"}
                )

            all_results = []
            for kb in target_kbs:
                try:
                    rag_service = rag_service_builder(kb)
                    if rag_service:
                        hits = rag_service.query(query)
                        for hit in hits:
                            all_results.append({
                                "kb_name": kb,
                                "source": hit.metadata.get("source_path", ""),
                                "content": hit.text[:500],
                                "score": getattr(hit, "score", None)
                            })
                except Exception:
                    continue

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "strategy": "RAG (legacy)",
                    "results": all_results,
                    "count": len(all_results),
                    "searched_kbs": target_kbs
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=f"搜索失败：{str(e)}")

    def list_kb_files(kb_name: str, file_type: str = "all", limit: int = 50) -> ToolResult:
        """列出知识库中的文件"""
        try:
            config = config_loader()
            kbs = config.get("knowledge_bases", [])

            kb_entry = next((kb for kb in kbs if kb.get("name") == kb_name), None)
            if not kb_entry:
                available = [kb.get("name", "") for kb in kbs]
                return ToolResult(
                    success=False,
                    error=f"知识库 '{kb_name}' 不存在。可用的知识库：{available}",
                    data={"suggestion": "list_knowledge_bases", "available_kbs": available}
                )

            from pathlib import Path
            kb_path = Path(kb_entry.get("path", ""))

            if not kb_path.exists():
                return ToolResult(
                    success=False,
                    error=f"知识库路径不存在：{kb_path}。请检查配置或重新添加知识库。",
                    data={"path": str(kb_path)}
                )

            # 文件类型分类
            IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
            TEXT_EXTS = {'.txt', '.md', '.py', '.js', '.ts', '.json', '.yaml', '.yml',
                        '.html', '.css', '.xml', '.csv', '.log', '.sh', '.bat',
                        '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.php', '.sql'}
            DOC_EXTS = {'.pdf', '.docx', '.pptx', '.xlsx'}

            def get_file_type(ext: str) -> str:
                ext = ext.lower()
                if ext in IMAGE_EXTS:
                    return "image"
                elif ext in TEXT_EXTS:
                    return "text"
                elif ext in DOC_EXTS:
                    return "document"
                return "other"

            files = []
            total_count = 0

            for f in kb_path.rglob("*"):
                if not f.is_file():
                    continue

                ext = f.suffix.lower()
                ftype = get_file_type(ext)

                if file_type != "all" and ftype != file_type:
                    continue

                total_count += 1
                if len(files) < limit:
                    try:
                        size_kb = round(f.stat().st_size / 1024, 1)
                    except:
                        size_kb = 0

                    files.append({
                        "name": f.name,
                        "path": str(f),
                        "type": ftype,
                        "extension": ext,
                        "size_kb": size_kb
                    })

            return ToolResult(
                success=True,
                data={
                    "kb_name": kb_name,
                    "kb_path": str(kb_path),
                    "files": files,
                    "returned_count": len(files),
                    "total_count": total_count,
                    "filter": file_type,
                    "note": "使用 read_file 工具读取文件内容（支持文本和图片）" if files else "没有找到匹配的文件"
                }
            )
        except Exception as e:
            return ToolResult(success=False, error=f"获取文件列表失败：{str(e)}")

    # ================================================================
    # 工具定义（改进后的描述）
    # ================================================================

    tools = [
        Tool(
            name="list_knowledge_bases",
            description="""列出所有知识库或获取指定知识库的详细信息。

## 使用场景
- 查看有哪些知识库可用
- 检查知识库是否激活
- 获取知识库的文件统计信息

## 参数说明
- kb_name: 指定则返回该知识库详情，不指定则列出全部
- include_stats: 是否包含文件数量统计（列表模式）

## 返回信息
- 知识库名称、路径、激活状态
- 文件数量、总大小、文件类型分布
- 检索策略（RAG 或 Context Packing）""",
            category=ToolCategory.KNOWLEDGE,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "kb_name": {
                        "type": "string",
                        "description": "知识库名称。指定则返回详情，不指定则列出全部"
                    },
                    "include_stats": {
                        "type": "boolean",
                        "description": "是否包含文件统计（仅列表模式有效）",
                        "default": False
                    }
                },
                "required": []
            },
            handler=list_knowledge_bases
        ),
        Tool(
            name="search_knowledge_base",
            description="""在知识库中搜索专业知识和文档内容。

## 使用场景
- 用户询问专业技术问题（如"光刻技术原理"）
- 需要引用具体文档内容
- 用户明确要求查询知识库

## 不适用场景
- 简单问候或闲聊（如"你好"、"1+1"）
- 用户询问你的配置 → 用 get_system_config
- 用户让你记住信息 → 用 remember_fact
- 知识库包含图片 → 先用 list_kb_files 获取路径，再用 read_file

## 返回信息
- strategy: 检索策略（RAG 或 Context Packing）
- context: 相关上下文内容
- search_results: 匹配的文档片段列表

## 搜索技巧
使用具体、专业的术语效果更好：
- ✅ "光刻技术原理"
- ❌ "那个技术怎么回事"（太模糊）""",
            category=ToolCategory.KNOWLEDGE,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或问题。使用具体术语效果更好"
                    },
                    "kb_name": {
                        "type": "string",
                        "description": "指定知识库名称。不指定则搜索所有激活的知识库"
                    }
                },
                "required": ["query"]
            },
            handler=search_knowledge_base
        ),
        Tool(
            name="list_kb_files",
            description="""列出知识库中的文件列表。

## 使用场景
- 知识库包含图片、PDF 等无法直接搜索的文件
- 需要浏览知识库的文件结构
- 查找特定类型的文件

## 典型工作流
1. list_kb_files(kb_name="图片库", file_type="image") → 获取图片列表
2. read_file(file_path="xxx.png") → 读取并查看图片

## 文件类型
- all: 全部文件
- image: 图片（png, jpg, gif, webp, bmp）
- text: 文本（txt, md, py, js 等）
- document: 文档（pdf, docx, pptx, xlsx）""",
            category=ToolCategory.KNOWLEDGE,
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {
                    "kb_name": {
                        "type": "string",
                        "description": "知识库名称。先用 list_knowledge_bases 查看可用知识库"
                    },
                    "file_type": {
                        "type": "string",
                        "enum": ["all", "image", "text", "document"],
                        "description": "文件类型过滤",
                        "default": "all"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回数量限制",
                        "default": 50
                    }
                },
                "required": ["kb_name"]
            },
            handler=list_kb_files
        )
    ]

    return tools


def register_kb_tools(
    config_loader: Callable,
    rag_service_builder: Callable = None,
    knowledge_manager: object = None,
) -> None:
    """注册KB工具到全局注册表"""
    registry = get_registry()
    tools = create_kb_tools(config_loader, rag_service_builder, knowledge_manager)
    for tool in tools:
        registry.register(tool)
