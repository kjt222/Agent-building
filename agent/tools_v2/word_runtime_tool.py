"""WordRuntimeEdit: structural .docx edits routed through a real Word engine.

Use this tool for any edit that touches TOC, heading boundaries, fields,
cross-references, numbering, or page numbers. Pure body-text edits inside a
single paragraph stay on the cheaper XML path (``WordEdit``), because they
do not need a live Word runtime to maintain consistency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.core.word_runtime import (
    AnchorMode,
    BackendUnavailable,
    FileLockedByOther,
    OpKind,
    UnknownAnchor,
    WordRuntimeError,
    WordRuntimeOp,
    WordRuntimeRequest,
    get_session_manager,
)
from agent.core.word_runtime.com_backend import make_default_com_backend_factory
from agent.tools_v2.primitives import _ToolBase, _resolve_guarded_path


_STRUCTURAL_OPS = {
    OpKind.REPLACE_IN_HEADING.value,
    OpKind.INSERT_PARAGRAPH_AFTER_HEADING.value,
    OpKind.SET_HEADING_TEXT.value,
    OpKind.REFRESH_FIELDS.value,
    OpKind.ADD_TOC.value,
    OpKind.GET_STRUCTURE.value,
}


def _resolve_docx_path(raw_path: Any, ctx: LoopContext | None) -> Path:
    if raw_path is None:
        raise ValueError("path is required")
    path = _resolve_guarded_path(str(raw_path), ctx) if ctx else Path(str(raw_path)).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if path.is_dir():
        raise IsADirectoryError(str(path))
    if path.suffix.lower() != ".docx":
        raise ValueError("expected .docx document")
    return path


def _coerce_op(raw: dict) -> WordRuntimeOp:
    op_name = str(raw.get("op") or "").strip()
    if op_name not in _STRUCTURAL_OPS:
        raise ValueError(
            f"WordRuntimeEdit only supports structural ops; got {op_name!r}. "
            "For pure text-in-paragraph changes use WordEdit instead."
        )
    if "paragraph_index" in raw:
        raise ValueError(
            "WordRuntimeEdit does not accept paragraph_index anchors. "
            "Use anchor_heading (heading text) so structural shifts cannot break the anchor."
        )
    return WordRuntimeOp(
        op=OpKind(op_name),
        anchor_mode=AnchorMode.HEADING_TEXT,
        anchor=raw.get("anchor_heading") or raw.get("anchor"),
        new_text=raw.get("new_text") or raw.get("text"),
        style=raw.get("style"),
        level=raw.get("level"),
        levels=raw.get("levels"),
        title=raw.get("title"),
    )


class WordRuntimeEditTool(_ToolBase):
    name = "WordRuntimeEdit"
    description = (
        "Structure-aware Word edit through the real Word engine (COM). Use this "
        "when the change touches TOC, heading boundaries, headers/footers, page "
        "numbers, fields, cross-references, or numbering. Word itself maintains "
        "field cache, TOC, numbering, and styles consistency. For plain "
        "text-in-paragraph edits prefer the cheaper WordEdit tool. Anchors are "
        "always heading text (never paragraph_index) because structural edits "
        "shift indexes."
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
                            "enum": sorted(_STRUCTURAL_OPS),
                        },
                        "anchor_heading": {
                            "type": "string",
                            "description": "Exact heading text used as anchor.",
                        },
                        "new_text": {"type": "string"},
                        "style": {"type": "string"},
                        "level": {"type": "integer"},
                        "levels": {
                            "type": "string",
                            "description": "TOC heading-level range, e.g. '1-3'.",
                        },
                        "title": {
                            "type": "string",
                            "description": "Optional TOC title heading inserted above the field.",
                        },
                    },
                    "required": ["op"],
                },
            },
            "save": {"type": "boolean", "default": True},
            "refresh_fields": {"type": "boolean", "default": True},
            "keep_backups": {"type": "integer", "default": 3},
        },
        "required": ["path", "ops"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    def _conversation_id(self, ctx: LoopContext) -> str:
        cid = (
            ctx.scratch.get("conversation_id")
            or ctx.scratch.get("conv_id")
            or getattr(ctx.config, "conversation_id", None)
            or "default"
        )
        return str(cid)

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        try:
            path = _resolve_docx_path(input.get("path"), ctx)
            raw_ops = input.get("ops") or []
            if not isinstance(raw_ops, list) or not raw_ops:
                raise ValueError("ops must be a non-empty list")
            ops = [_coerce_op(item) for item in raw_ops]
            for op in ops:
                op.validate_for_anchor()

            request = WordRuntimeRequest(
                path=path,
                ops=ops,
                conversation_id=self._conversation_id(ctx),
                save=bool(input.get("save", True)),
                refresh_fields_on_save=bool(input.get("refresh_fields", True)),
                keep_backups=int(input.get("keep_backups", 3) or 3),
            )

            manager = get_session_manager(make_default_com_backend_factory())
            try:
                manager.sweep_idle()
            except Exception:
                pass
            session = manager.get_session(request.conversation_id)
            result = session.apply(request)
        except BackendUnavailable as exc:
            return self._err(
                f"WordRuntime backend unavailable: {exc}. Falling back is the "
                "agent's responsibility (use WordEdit for plain text or run on a "
                "Windows host with Word installed)."
            )
        except FileLockedByOther as exc:
            return self._err(
                f"FileLockedByOther: {exc}. Close the document in Word and retry."
            )
        except UnknownAnchor as exc:
            return self._err(f"UnknownAnchor: {exc}")
        except WordRuntimeError as exc:
            return self._err(f"WordRuntimeError: {exc}")
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")

        ctx.scratch.setdefault("word_runtime_files", set()).add(str(path))
        ctx.scratch["word_runtime_last_file"] = str(path)
        ctx.scratch.setdefault("edited_files", set()).add(str(path))
        try:
            from agent.core.artifact_context import register_word_artifact
            register_word_artifact(
                self._conversation_id(ctx),
                str(path),
                result.structure_after or {},
            )
        except Exception:
            pass
        return self._ok(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))


def word_runtime_toolset() -> dict:
    tool = WordRuntimeEditTool()
    return {tool.name: tool}
