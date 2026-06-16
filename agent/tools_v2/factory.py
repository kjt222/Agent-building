"""Build tools by name from runtime configuration.

Skills declare tool names; this module knows how to instantiate each one.
"""

from __future__ import annotations

from typing import Any

from agent.tools_v2.primitives import default_toolset


class _UnavailableTool:
    """Placeholder for a tool whose implementation module is missing.

    Some optional tools (web_tool, image_tool, the Excel COM runtime) were
    lost in the D-drive-format recovery and have no surviving spec to rebuild
    from. Rather than crash the whole tool-build with a cryptic
    ``ModuleNotFoundError`` when a skill/config still references one, we hand
    back this stub: it builds cleanly and, if the model ever calls it, returns
    a clear actionable error instead of 500-ing the turn.
    """

    parallel_safe = True

    def __init__(self, name: str, reason: str):
        from agent.core.loop import PermissionLevel

        self.name = name
        self.description = f"(unavailable) {reason}"
        self.input_schema = {"type": "object"}
        self.permission_level = PermissionLevel.SAFE
        self._reason = reason

    async def run(self, input: dict, ctx: Any):
        from agent.core.loop import ToolResultBlock

        return ToolResultBlock(
            tool_use_id="",
            content=(
                f"Tool '{self.name}' is unavailable in this build: "
                f"{self._reason}. Proceed without it or tell the user it is "
                f"not installed."
            ),
            is_error=True,
        )


def build_tool(name: str, app_cfg: dict | None = None) -> Any:
    """Return a tool instance for ``name``. Raises ``KeyError`` if unknown."""
    base = default_toolset()
    if name in base:
        return base[name]
    cfg = app_cfg or {}
    if name == "WordRead":
        from agent.tools_v2.word_tool import WordReadTool
        return WordReadTool()
    if name == "WordEdit":
        from agent.tools_v2.word_tool import WordEditTool
        return WordEditTool()
    if name == "WordRuntimeEdit":
        from agent.tools_v2.word_runtime_tool import WordRuntimeEditTool
        return WordRuntimeEditTool()
    if name == "ExcelRead":
        from agent.tools_v2.excel_tool import ExcelReadTool
        return ExcelReadTool()
    if name == "ExcelEdit":
        from agent.tools_v2.excel_tool import ExcelEditTool
        return ExcelEditTool()
    if name == "RenderDocument":
        from agent.tools_v2.render_tool import RenderDocumentTool
        return RenderDocumentTool()
    if name == "KnowledgeSearch":
        from agent.tools_v2.knowledge_tool import KnowledgeSearchTool
        return KnowledgeSearchTool()
    if name == "KnowledgeIndex":
        from agent.tools_v2.knowledge_tool import KnowledgeIndexTool
        return KnowledgeIndexTool()
    if name == "WebSearch":
        try:
            from agent.tools_v2.web_tool import WebSearchTool
            return WebSearchTool()
        except ImportError:
            return _UnavailableTool(
                "WebSearch", "the web_tool module is not installed"
            )
    if name == "FetchURL":
        try:
            from agent.tools_v2.web_tool import FetchURLTool
            return FetchURLTool()
        except ImportError:
            return _UnavailableTool(
                "FetchURL", "the web_tool module is not installed"
            )
    if name == "Verify":
        from agent.tools_v2.verify_tool import VerifyTool
        return VerifyTool()
    if name == "FileVerify":
        from agent.tools_v2.file_verify_tool import FileVerifyTool
        return FileVerifyTool()
    if name == "Image":
        return _build_image_tool(cfg)
    if name == "obsidian_read_excalidraw_canvas":
        from agent.tools_capability.obsidian.canvas_tools import ReadExcalidrawCanvasTool
        return ReadExcalidrawCanvasTool()
    if name == "obsidian_write_excalidraw_elements":
        from agent.tools_capability.obsidian.canvas_tools import WriteExcalidrawElementsTool
        return WriteExcalidrawElementsTool()
    if name == "obsidian_find_pdf_text_anchor":
        from agent.tools_capability.obsidian.pdf_anchor import FindPdfTextAnchorTool
        return FindPdfTextAnchorTool()
    if name == "obsidian_refresh_note":
        from agent.tools_capability.obsidian.refresh_note import RefreshNoteTool
        return RefreshNoteTool()
    if name == "obsidian_add_formula_annotation":
        from agent.tools_capability.obsidian.formula_annotation import (
            AddFormulaAnnotationTool,
        )
        return AddFormulaAnnotationTool()
    raise KeyError(f"unknown tool: {name}")


def _build_image_tool(app_cfg: dict) -> Any:
    from agent.credentials import resolve_api_key

    try:
        from agent.tools_v2.image_tool import ImageTool
    except ImportError:
        return _UnavailableTool("Image", "the image_tool module is not installed")

    image_cfg = app_cfg.get("image_generation") or {}
    semantic = image_cfg.get("semantic_review") or {}
    image_api_key = resolve_api_key(
        api_key_env=image_cfg.get("api_key_env") or "OPENAI_IMAGE_API_KEY",
        api_key_ref=image_cfg.get("api_key_ref") or None,
    )
    review_api_key = resolve_api_key(
        api_key_env=(
            semantic.get("api_key_env")
            or image_cfg.get("api_key_env")
            or "OPENAI_API_KEY"
        ),
        api_key_ref=(
            semantic.get("api_key_ref")
            or image_cfg.get("api_key_ref")
            or None
        ),
    )
    return ImageTool(
        provider=str(image_cfg.get("provider") or "openai"),
        api_key=image_api_key,
        base_url=image_cfg.get("base_url") or None,
        default_model=str(image_cfg.get("model") or "gpt-image-2"),
        max_images_per_turn=int(image_cfg.get("max_images_per_turn") or 2),
        max_estimated_cost_usd_per_turn=float(
            image_cfg.get("max_estimated_cost_usd_per_turn") or 0.25
        ),
        semantic_review_enabled=bool(semantic.get("enabled", False)),
        semantic_review_api_key=review_api_key,
        semantic_review_base_url=(
            semantic.get("base_url")
            or image_cfg.get("base_url")
            or None
        ),
        semantic_review_model=str(semantic.get("model") or "") or None,
        semantic_review_max_per_turn=int(semantic.get("max_per_turn") or 1),
        semantic_repair_enabled=bool(semantic.get("auto_repair", False)),
        semantic_repair_max_attempts=int(semantic.get("max_repair_attempts") or 1),
        cache_ttl_seconds=int(image_cfg.get("cache_ttl_seconds") or 86_400),
    )


def build_tools(names: list[str], app_cfg: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in names:
        if name in out:
            continue
        out[name] = build_tool(name, app_cfg)
    return out
