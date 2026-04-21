"""Core Claude-Code-style primitives: Bash, Read, Write, Edit, Glob, Grep."""

from __future__ import annotations

import asyncio
import fnmatch
import os
import re
import subprocess
from pathlib import Path

from agent.core.loop import (
    LoopContext,
    PermissionLevel,
    ToolResultBlock,
)


# ---------------------------------------------------------------------------
# Base mixin
# ---------------------------------------------------------------------------

class _ToolBase:
    name: str = ""
    description: str = ""
    input_schema: dict = {}
    permission_level: PermissionLevel = PermissionLevel.SAFE
    parallel_safe: bool = True

    def _ok(self, text: str, tool_use_id: str = "") -> ToolResultBlock:
        return ToolResultBlock(tool_use_id=tool_use_id, content=text, is_error=False)

    def _err(self, text: str, tool_use_id: str = "") -> ToolResultBlock:
        return ToolResultBlock(tool_use_id=tool_use_id, content=text, is_error=True)


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------

class BashTool(_ToolBase):
    name = "Bash"
    description = (
        "Execute a shell command and return its stdout/stderr. "
        "Use for running scripts, git, file operations that don't fit other tools. "
        "Prefer dedicated tools (Read/Write/Edit/Grep/Glob) when they apply."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "timeout": {"type": "number", "description": "Seconds (default 60)", "default": 60},
        },
        "required": ["command"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        cmd = input.get("command", "")
        timeout = float(input.get("timeout", 60))
        if not cmd:
            return self._err("empty command")
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return self._err(f"timeout after {timeout}s")
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        body = out
        if err:
            body += f"\n[stderr]\n{err}"
        body += f"\n[exit={proc.returncode}]"
        return self._ok(body) if proc.returncode == 0 else self._err(body)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

class ReadTool(_ToolBase):
    name = "Read"
    description = (
        "Read a text file. Returns content with line numbers. "
        "Use offset+limit to read slices of large files."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "1-indexed start line", "default": 1},
            "limit": {"type": "integer", "description": "max lines (default 2000)", "default": 2000},
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        path = Path(input["path"])
        offset = max(1, int(input.get("offset", 1)))
        limit = int(input.get("limit", 2000))
        if not path.exists():
            return self._err(f"file not found: {path}")
        if path.is_dir():
            return self._err(f"is a directory: {path}")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            return self._err(f"read failed: {exc}")
        chunk = lines[offset - 1 : offset - 1 + limit]
        out = "\n".join(f"{offset + i}\t{line}" for i, line in enumerate(chunk))
        # Track read for Edit contract
        ctx.scratch.setdefault("read_files", set()).add(str(path.resolve()))
        return self._ok(out)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

class WriteTool(_ToolBase):
    name = "Write"
    description = (
        "Write (or overwrite) a file with the given content. "
        "Prefer Edit for modifying existing files. "
        "If the file exists, it must have been Read first in this session."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        path = Path(input["path"])
        read_files: set = ctx.scratch.setdefault("read_files", set())
        if path.exists() and str(path.resolve()) not in read_files:
            return self._err(
                f"file exists but was not read in this session: {path}. Read it first."
            )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(input["content"], encoding="utf-8")
        except Exception as exc:
            return self._err(f"write failed: {exc}")
        read_files.add(str(path.resolve()))
        return self._ok(f"wrote {len(input['content'])} chars to {path}")


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------

class EditTool(_ToolBase):
    name = "Edit"
    description = (
        "Replace an exact string in a file. old_string must be unique in the "
        "file (set replace_all=true for every occurrence). File must have been "
        "Read first in this session."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old_string", "new_string"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        path = Path(input["path"])
        read_files: set = ctx.scratch.setdefault("read_files", set())
        if str(path.resolve()) not in read_files:
            return self._err(f"file not read in this session: {path}. Read it first.")
        if not path.exists():
            return self._err(f"file not found: {path}")
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return self._err(f"read failed: {exc}")
        old = input["old_string"]
        new = input["new_string"]
        if old not in content:
            return self._err("old_string not found")
        count = content.count(old)
        if count > 1 and not input.get("replace_all", False):
            return self._err(
                f"old_string appears {count} times; narrow it or set replace_all=true"
            )
        new_content = content.replace(old, new)
        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            return self._err(f"write failed: {exc}")
        return self._ok(f"replaced {count if input.get('replace_all') else 1} occurrence(s)")


# ---------------------------------------------------------------------------
# Glob
# ---------------------------------------------------------------------------

class GlobTool(_ToolBase):
    name = "Glob"
    description = (
        "Find files by glob pattern (e.g. '**/*.py'). Returns paths sorted by mtime desc."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "search root (default cwd)"},
        },
        "required": ["pattern"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        root = Path(input.get("path") or os.getcwd())
        pattern = input["pattern"]
        try:
            matches = list(root.glob(pattern))
        except Exception as exc:
            return self._err(f"glob failed: {exc}")
        matches = [m for m in matches if m.is_file()]
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            return self._ok("(no matches)")
        return self._ok("\n".join(str(m) for m in matches[:500]))


# ---------------------------------------------------------------------------
# Grep
# ---------------------------------------------------------------------------

class GrepTool(_ToolBase):
    name = "Grep"
    description = (
        "Search file contents with a regex. Returns matching file paths "
        "(set output_mode='content' for line-level matches)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string", "description": "filter files by glob"},
            "output_mode": {"type": "string", "enum": ["files", "content"], "default": "files"},
            "max_results": {"type": "integer", "default": 100},
        },
        "required": ["pattern"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        pattern = input["pattern"]
        root = Path(input.get("path") or os.getcwd())
        glob = input.get("glob") or "**/*"
        mode = input.get("output_mode", "files")
        limit = int(input.get("max_results", 100))

        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return self._err(f"invalid regex: {exc}")

        results: list[str] = []
        for path in root.glob(glob):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if mode == "files":
                if regex.search(text):
                    results.append(str(path))
                    if len(results) >= limit:
                        break
            else:  # content
                for i, line in enumerate(text.splitlines(), 1):
                    if regex.search(line):
                        results.append(f"{path}:{i}: {line}")
                        if len(results) >= limit:
                            break
                if len(results) >= limit:
                    break
        if not results:
            return self._ok("(no matches)")
        return self._ok("\n".join(results))


# ---------------------------------------------------------------------------
# Registry helper
# ---------------------------------------------------------------------------

def default_toolset() -> dict:
    tools = [BashTool(), ReadTool(), WriteTool(), EditTool(), GlobTool(), GrepTool()]
    return {t.name: t for t in tools}


def full_toolset() -> dict:
    """Default primitives + domain tools (docx, knowledge base)."""
    from agent.tools_v2.docx_tool import DocxEditTool
    from agent.tools_v2.knowledge_tool import KnowledgeIndexTool, KnowledgeSearchTool

    tools = default_toolset()
    tools["DocxEdit"] = DocxEditTool()
    tools["KnowledgeSearch"] = KnowledgeSearchTool()
    tools["KnowledgeIndex"] = KnowledgeIndexTool()
    return tools
