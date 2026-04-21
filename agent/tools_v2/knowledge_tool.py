"""KnowledgeSearch / KnowledgeIndex — v2 tools wrapping KnowledgeManager.

KnowledgeSearch (SAFE, parallel_safe): read-only. Supports actions
    search | list | info. Returns FTS5-ranked snippets by default; RAG hybrid
    path activates automatically when KB size exceeds the manager's threshold
    AND an embedder + vec_store are configured on the singleton manager.

KnowledgeIndex (NEEDS_APPROVAL): indexes a directory into a named KB. Idempotent
    — unchanged files are skipped via content hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.storage.knowledge_manager import KnowledgeManager
from agent.tools_v2.primitives import _ToolBase


_DEFAULT_EXTENSIONS = [
    ".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".html", ".css", ".rst", ".ini", ".toml",
]


def _get_manager(ctx: LoopContext) -> KnowledgeManager:
    """Reuse a shared KnowledgeManager per loop run via ctx.scratch."""
    mgr: Optional[KnowledgeManager] = ctx.scratch.get("knowledge_manager")
    if mgr is None:
        mgr = KnowledgeManager()
        ctx.scratch["knowledge_manager"] = mgr
    return mgr


def _list_kb_names(mgr: KnowledgeManager) -> list[str]:
    cur = mgr.db.conn.execute(
        "SELECT kb_name, COUNT(*) AS n, SUM(token_count) AS tokens "
        "FROM file_index GROUP BY kb_name ORDER BY kb_name"
    )
    return [dict(row) for row in cur]


class KnowledgeSearchTool(_ToolBase):
    name = "KnowledgeSearch"
    description = (
        "Search indexed knowledge bases (FTS5 with optional hybrid vector "
        "search when configured), list KBs, or inspect one.\n"
        "  action='search' — required: query. Optional: kb_names (filter), "
        "limit. Returns ranked snippets.\n"
        "  action='list'   — returns all KBs with file count and token totals.\n"
        "  action='info'   — required: kb_name. Returns stats + file listing "
        "+ recommended retrieval strategy (Context Packing vs Hybrid/FTS5).\n"
        "To ingest a folder first, use KnowledgeIndex."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "list", "info"],
                "default": "search",
            },
            "query": {"type": "string", "description": "FTS5 query (action=search)"},
            "kb_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict search to these KBs (action=search).",
            },
            "kb_name": {"type": "string", "description": "Single KB (action=info)"},
            "limit": {"type": "integer", "default": 10},
        },
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        action = input.get("action", "search")
        mgr = _get_manager(ctx)

        if action == "list":
            rows = _list_kb_names(mgr)
            if not rows:
                return self._ok("(no knowledge bases indexed)")
            lines = [
                f"- {r['kb_name']}: {r['n']} files, {r['tokens'] or 0} tokens"
                for r in rows
            ]
            return self._ok("\n".join(lines))

        if action == "info":
            kb_name = input.get("kb_name")
            if not kb_name:
                return self._err("action=info requires kb_name")
            info = mgr.get_kb_info(kb_name)
            if info["file_count"] == 0:
                return self._err(f"kb {kb_name!r} not found or empty")
            strategy = mgr.retrieval_strategy([kb_name])
            head = (
                f"kb={kb_name} files={info['file_count']} "
                f"tokens={info['total_tokens']} bytes={info['total_bytes']} "
                f"strategy={strategy}"
            )
            files = info.get("files", [])[:50]
            file_lines = [f"  {f['filename']} ({f['token_count']} tok)" for f in files]
            more = f"\n  …+{len(info['files']) - 50} more" if len(info.get("files", [])) > 50 else ""
            return self._ok(head + "\nfiles:\n" + "\n".join(file_lines) + more)

        # default: search
        query = input.get("query")
        if not query:
            return self._err("action=search requires query")
        kb_names = input.get("kb_names")
        limit = int(input.get("limit", 10))
        try:
            results = mgr.search(query, kb_names=kb_names, limit=limit)
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")
        if not results:
            return self._ok("(no matches)")
        lines = []
        for r in results:
            snippet = (r.get("snippet") or "").replace("\n", " ")
            lines.append(f"[{r['kb_name']}/{r['filename']} #{r['id']}] {snippet}")
        return self._ok("\n".join(lines))


class KnowledgeIndexTool(_ToolBase):
    name = "KnowledgeIndex"
    description = (
        "Index a directory as a knowledge base. Extracts text per file, stores "
        "in SQLite FTS5, and (if embedder configured) embeds chunks for vector "
        "search. Idempotent: unchanged files are skipped via content hash.\n"
        "Input: kb_name, directory (absolute or repo-relative), "
        "extensions (optional list; defaults to common text/code formats)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "kb_name": {"type": "string"},
            "directory": {"type": "string"},
            "extensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "File extensions to include (e.g. ['.md','.py']).",
            },
        },
        "required": ["kb_name", "directory"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        kb_name = input.get("kb_name")
        directory = input.get("directory")
        if not kb_name or not directory:
            return self._err("kb_name and directory are required")
        path = Path(directory)
        if not path.exists():
            return self._err(f"directory not found: {path}")
        if not path.is_dir():
            return self._err(f"not a directory: {path}")
        exts = input.get("extensions") or _DEFAULT_EXTENSIONS
        mgr = _get_manager(ctx)
        try:
            stats = mgr.index_directory(kb_name, path, extensions=list(exts))
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")
        summary = (
            f"kb={kb_name} indexed={stats.get('indexed', 0)} "
            f"skipped={stats.get('skipped', 0)} "
            f"embedded={stats.get('embedded', 0)} "
            f"errors={len(stats.get('errors', []))}"
        )
        errs = stats.get("errors") or []
        if errs:
            summary += "\nfirst errors:\n  " + "\n  ".join(errs[:5])
        return self._ok(summary)
