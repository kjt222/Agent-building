"""Minimal Word tools for AgentLoop v2.

The Word boundary mirrors the Excel Read/Edit contract. The model can inspect
document structure first, then apply scoped operations. It cannot run arbitrary
python-docx or COM automation scripts through this tool.
"""

from __future__ import annotations

import json
import shutil
import time
from copy import copy
from pathlib import Path
from typing import Any

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.tools.docx_editor import _replace_in_paragraph
from agent.tools_v2.primitives import _ToolBase


_SUPPORTED_SUFFIXES = {".docx"}
_MAX_PARAGRAPHS = 120
_MAX_TABLE_CELLS = 120


def _load_document(path: Path):
    import docx

    return docx.Document(str(path))


def _resolve_word_path(raw_path: Any) -> Path:
    if raw_path is None:
        raise ValueError("path is required")
    path = Path(str(raw_path)).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if path.is_dir():
        raise IsADirectoryError(str(path))
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ValueError("expected .docx document")
    return path


def _path_key(path: Path) -> str:
    return str(path.resolve())


def _paragraph_payload(paragraph, index: int, *, include_runs: bool) -> dict:
    payload = {
        "index": index,
        "text": paragraph.text,
        "style": paragraph.style.name if paragraph.style else None,
        "alignment": str(paragraph.alignment) if paragraph.alignment is not None else None,
    }
    if include_runs:
        runs = []
        for run_index, run in enumerate(paragraph.runs):
            runs.append({
                "index": run_index,
                "text": run.text,
                "bold": bool(run.bold),
                "italic": bool(run.italic),
                "underline": bool(run.underline),
                "font_name": run.font.name,
                "font_size": str(run.font.size) if run.font.size else None,
                "font_color": getattr(run.font.color, "rgb", None),
            })
        payload["runs"] = runs
    return payload


def _table_payload(table, index: int, *, max_cells: int) -> dict:
    cells = []
    count = 0
    truncated = False
    for row_index, row in enumerate(table.rows):
        for col_index, cell in enumerate(row.cells):
            if count >= max_cells:
                truncated = True
                break
            cells.append({
                "row": row_index,
                "col": col_index,
                "text": cell.text,
            })
            count += 1
        if truncated:
            break
    return {
        "index": index,
        "rows": len(table.rows),
        "columns": len(table.columns),
        "cells": cells,
        "truncated": truncated,
    }


def _copy_paragraph_style(document, source_index: int, target_index: int) -> str:
    source = document.paragraphs[source_index]
    target = document.paragraphs[target_index]
    if source.style:
        target.style = source.style
    target.alignment = source.alignment
    target.paragraph_format.left_indent = source.paragraph_format.left_indent
    target.paragraph_format.right_indent = source.paragraph_format.right_indent
    target.paragraph_format.first_line_indent = source.paragraph_format.first_line_indent
    target.paragraph_format.space_before = source.paragraph_format.space_before
    target.paragraph_format.space_after = source.paragraph_format.space_after
    target.paragraph_format.line_spacing = source.paragraph_format.line_spacing

    source_run = next((run for run in source.runs if run.text), None)
    if source_run is not None:
        for run in target.runs:
            run.bold = source_run.bold
            run.italic = source_run.italic
            run.underline = source_run.underline
            run.font.name = source_run.font.name
            run.font.size = source_run.font.size
            source_color = getattr(source_run.font.color, "rgb", None)
            if source_color is not None:
                run.font.color.rgb = source_color
    return f"paragraph_style:{source_index}->{target_index}"


def _set_run_style(paragraph, op: dict) -> int:
    touched = 0
    for run in paragraph.runs:
        if "bold" in op:
            run.bold = bool(op["bold"])
        if "italic" in op:
            run.italic = bool(op["italic"])
        if "underline" in op:
            run.underline = bool(op["underline"])
        if "font_name" in op:
            run.font.name = str(op["font_name"])
        if "font_size_pt" in op:
            from docx.shared import Pt

            run.font.size = Pt(float(op["font_size_pt"]))
        touched += 1
    return touched


def _insert_paragraph_after(paragraph, text: str, style: str | None = None):
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    new_p = OxmlElement("w:p")
    paragraph._p.addnext(new_p)
    inserted = Paragraph(new_p, paragraph._parent)
    inserted.clear()
    inserted.add_run(text)
    if style:
        inserted.style = style
    return inserted


class WordReadTool(_ToolBase):
    name = "WordRead"
    description = (
        "Inspect a .docx document before editing. Returns paragraph indexes, "
        "text, styles, run-level formatting, and table cell text. Use this "
        "before WordEdit."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "DOCX path"},
            "paragraph_start": {"type": "integer", "default": 0},
            "paragraph_limit": {"type": "integer", "default": 80},
            "include_runs": {"type": "boolean", "default": True},
            "include_tables": {"type": "boolean", "default": True},
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        try:
            path = _resolve_word_path(input.get("path"))
            document = _load_document(path)
            start = max(0, int(input.get("paragraph_start", 0)))
            limit = max(1, min(int(input.get("paragraph_limit", 80)), _MAX_PARAGRAPHS))
            include_runs = bool(input.get("include_runs", True))
            include_tables = bool(input.get("include_tables", True))
            paragraphs = [
                _paragraph_payload(p, idx, include_runs=include_runs)
                for idx, p in enumerate(document.paragraphs)
                if start <= idx < start + limit
            ]
            tables = []
            if include_tables:
                tables = [
                    _table_payload(table, idx, max_cells=_MAX_TABLE_CELLS)
                    for idx, table in enumerate(document.tables)
                ]
            result = {
                "type": "word_read",
                "path": str(path),
                "paragraph_count": len(document.paragraphs),
                "table_count": len(document.tables),
                "paragraphs": paragraphs,
                "tables": tables,
            }
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")

        ctx.scratch.setdefault("word_read_files", set()).add(_path_key(path))
        return self._ok(json.dumps(result, ensure_ascii=False, indent=2, default=str))


class WordEditTool(_ToolBase):
    name = "WordEdit"
    description = (
        "Apply scoped structured edits to a .docx document. The document must "
        "have been inspected with WordRead in this AgentLoop run. Prefer "
        "paragraph_index-scoped operations; global text replacement is rejected "
        "unless allow_global=true."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "DOCX path"},
            "ops": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": [
                                "replace_text",
                                "set_paragraph_text",
                                "append_paragraph",
                                "add_heading",
                                "insert_paragraph_after",
                                "copy_paragraph_style",
                                "set_run_style",
                            ],
                        },
                        "paragraph_index": {"type": "integer"},
                        "source_paragraph_index": {"type": "integer"},
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                        "text": {"type": "string"},
                        "style": {"type": "string"},
                        "level": {"type": "integer"},
                        "bold": {"type": "boolean"},
                        "italic": {"type": "boolean"},
                        "underline": {"type": "boolean"},
                        "font_name": {"type": "string"},
                        "font_size_pt": {"type": "number"},
                    },
                    "required": ["op"],
                },
            },
            "backup": {"type": "boolean", "default": True},
            "allow_global": {"type": "boolean", "default": False},
            "strict": {"type": "boolean", "default": False},
        },
        "required": ["path", "ops"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        try:
            path = _resolve_word_path(input.get("path"))
            if _path_key(path) not in ctx.scratch.setdefault("word_read_files", set()):
                return self._err(
                    f"document was not inspected in this session: {path}. "
                    "Call WordRead first."
                )
            ops = input.get("ops")
            if not isinstance(ops, list) or not ops:
                return self._err("WordEdit requires a non-empty ops list")
            allow_global = bool(input.get("allow_global", False))
            strict = bool(input.get("strict", False))

            backup_path = None
            if bool(input.get("backup", True)):
                stamp = time.strftime("%Y%m%d%H%M%S")
                backup_path = path.with_name(f"{path.stem}.bak-{stamp}{path.suffix}")
                shutil.copy2(path, backup_path)

            document = _load_document(path)
            touched: list[str] = []
            for op in ops:
                if not isinstance(op, dict):
                    raise ValueError("each op must be an object")
                op_type = op.get("op")
                paragraph_index = op.get("paragraph_index")
                paragraph = None
                if paragraph_index is not None:
                    paragraph_index = int(paragraph_index)
                    if paragraph_index < 0 or paragraph_index >= len(document.paragraphs):
                        raise ValueError(f"paragraph_index out of range: {paragraph_index}")
                    paragraph = document.paragraphs[paragraph_index]

                if op_type == "replace_text":
                    if op.get("old") is None or op.get("new") is None:
                        raise ValueError("replace_text requires 'old' and 'new'")
                    if paragraph is None and not allow_global:
                        raise ValueError(
                            "replace_text requires paragraph_index unless "
                            "allow_global=true"
                        )
                    targets = [paragraph] if paragraph is not None else document.paragraphs
                    total = 0
                    merges = 0
                    for target in targets:
                        replaced, _, cross = _replace_in_paragraph(
                            target,
                            str(op["old"]),
                            str(op["new"]),
                            None,
                            strict,
                        )
                        total += replaced
                        merges += cross
                    touched.append(
                        f"replace_text:{paragraph_index if paragraph is not None else 'global'} "
                        f"replacements={total} cross_run_merges={merges}"
                    )
                elif op_type == "set_paragraph_text":
                    if paragraph is None:
                        raise ValueError("set_paragraph_text requires paragraph_index")
                    text = str(op.get("text") or "")
                    if paragraph.runs:
                        paragraph.runs[0].text = text
                        for run in paragraph.runs[1:]:
                            run.text = ""
                    else:
                        paragraph.add_run(text)
                    touched.append(f"paragraph:{paragraph_index}")
                elif op_type == "append_paragraph":
                    p = document.add_paragraph(str(op.get("text") or ""), style=op.get("style"))
                    touched.append(f"append_paragraph:{len(document.paragraphs) - 1}:{p.style.name}")
                elif op_type == "add_heading":
                    level = int(op.get("level", 1))
                    document.add_heading(str(op.get("text") or ""), level=level)
                    touched.append(f"add_heading:{level}")
                elif op_type == "insert_paragraph_after":
                    if paragraph is None:
                        raise ValueError("insert_paragraph_after requires paragraph_index")
                    _insert_paragraph_after(
                        paragraph,
                        str(op.get("text") or ""),
                        op.get("style"),
                    )
                    touched.append(f"insert_after:{paragraph_index}")
                elif op_type == "copy_paragraph_style":
                    source_index = op.get("source_paragraph_index")
                    if source_index is None or paragraph_index is None:
                        raise ValueError(
                            "copy_paragraph_style requires source_paragraph_index "
                            "and paragraph_index"
                        )
                    source_index = int(source_index)
                    paragraph_index = int(paragraph_index)
                    if source_index < 0 or source_index >= len(document.paragraphs):
                        raise ValueError(f"source_paragraph_index out of range: {source_index}")
                    touched.append(_copy_paragraph_style(document, source_index, paragraph_index))
                elif op_type == "set_run_style":
                    if paragraph is None:
                        raise ValueError("set_run_style requires paragraph_index")
                    count = _set_run_style(paragraph, op)
                    touched.append(f"set_run_style:{paragraph_index} runs={count}")
                else:
                    raise ValueError(f"unsupported WordEdit op: {op_type}")

            document.save(str(path))
            result = {
                "type": "word_edit",
                "path": str(path),
                "backup_path": str(backup_path) if backup_path else None,
                "ops_applied": len(ops),
                "touched": touched,
            }
            ctx.scratch.setdefault("word_edited_files", set()).add(_path_key(path))
            ctx.scratch.setdefault("edited_files", set()).add(_path_key(path))
            return self._ok(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")


def word_toolset() -> dict:
    tools = [WordReadTool(), WordEditTool()]
    return {tool.name: tool for tool in tools}
