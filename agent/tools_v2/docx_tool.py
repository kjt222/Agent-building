"""DocxEdit — wraps apply_docx_ops as a ToolProtocol-compatible tool.

Exposes run-level surgical edits. Cross-run matches use the format-preserving
merge strategy by default (see agent/tools/docx_editor.py). Pass
strict=True to require each match to fit within a single run.
"""

from __future__ import annotations

from pathlib import Path

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.tools.docx_editor import apply_docx_ops
from agent.tools_v2.primitives import _ToolBase


class DocxEditTool(_ToolBase):
    name = "DocxEdit"
    description = (
        "Edit a .docx file in place, preserving run-level formatting (bold, "
        "italic, font). Pass `ops` as a list of operations; each op is one of:\n"
        "  {op: 'replace_text', old, new, count?}\n"
        "  {op: 'append_paragraph', text, style?}\n"
        "  {op: 'add_heading', text, level?}\n"
        "Example input: {\"path\": \"a.docx\", \"ops\": [{\"op\": \"replace_text\", "
        "\"old\": \"foo\", \"new\": \"bar\"}]}. "
        "For 'replace_text', cross-run matches are merged into the first "
        "spanned run's format (the rest of that paragraph's formatting is "
        "unaffected). Set strict=true to raise instead."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the .docx"},
            "ops": {
                "type": "array",
                "description": "Ordered list of edit operations.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {
                            "type": "string",
                            "enum": ["replace_text", "append_paragraph", "add_heading"],
                        },
                        "old": {"type": "string"},
                        "new": {"type": "string"},
                        "count": {"type": "integer"},
                        "text": {"type": "string"},
                        "style": {"type": "string"},
                        "level": {"type": "integer"},
                    },
                    "required": ["op"],
                },
            },
            "strict": {"type": "boolean", "default": False},
        },
        "required": ["path", "ops"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        path = Path(input["path"])
        ops = input.get("ops")
        # Lenient fallback: some models flatten a single op to top level.
        if not ops and "op" in input:
            shortcut = {k: v for k, v in input.items() if k not in ("path", "strict")}
            ops = [shortcut]
        ops = ops or []
        strict = bool(input.get("strict", False))
        if not path.exists():
            return self._err(f"file not found: {path}")
        if path.suffix.lower() != ".docx":
            return self._err(f"not a .docx: {path}")
        try:
            result = apply_docx_ops(path, ops, fail_on_cross_run=strict)
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")
        summary = (
            f"replacements={result.replacements} "
            f"appended={result.appended} "
            f"headings={result.headings} "
            f"cross_run_merges={result.cross_run_merges}"
        )
        return self._ok(summary)
