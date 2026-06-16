"""Built-in hooks for the agent loop.

Provides:
- intent-without-action Stop hook;
- final delivery guard Stop hook;
- PreToolUse approval hook for NEEDS_APPROVAL tools.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from agent.core.loop import (
    LoopContext,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# Patterns that suggest the model announced an action but may not have emitted
# a tool_use. Chinese phrases use escapes so this file remains ASCII-stable.
_INTENT_PATTERN = re.compile(
    r"(?:\bI['’]?ll\b|\bI will\b|\blet me\b|\bnext,? I\b|"
    r"\bgoing to\b|\bI['’]?m going to\b|"
    r"\u63a5\u4e0b\u6765\u6211|"  # next I / then I
    r"\u4e0b\u4e00\u6b65\u6211|"
    r"\u4e0b\u4e00\u6b65\u53ef\u4ee5|"
    r"\u6211\u6765|"
    r"\u76f4\u63a5\u5e2e\u4f60|"
    r"\u9a6c\u4e0a\u5c31|"
    r"\u6211\u73b0\u5728\u6765|"
    r"\u6211\u4f1a|"
    r"\u8ba9\u6211\u4eec\u6765|"
    r"\u6211\u53ef\u4ee5|"
    r"\u5982\u679c\u4f60\u8981|"
    r"\u4f60\u56de\u590d|"
    r"\u8981\u6211)",
    re.IGNORECASE,
)

_NUDGE_TEXT = (
    "You announced an action but did not call a tool. Execute it now by "
    "calling the appropriate tool. Do not describe what you will do; do it."
)


def make_intent_without_action_hook(max_nudges: int = 2):
    """Return a StopHook that nudges when the model announces action only."""

    async def hook(ctx: LoopContext) -> None:
        if not ctx.messages:
            return
        last = ctx.messages[-1]
        if last.role != Role.ASSISTANT:
            return
        text = "".join(b.text for b in last.content if isinstance(b, TextBlock))
        has_tool_use = any(isinstance(b, ToolUseBlock) for b in last.content)
        if has_tool_use or not text:
            return
        if not _INTENT_PATTERN.search(text):
            return

        nudges = ctx.scratch.get("intent_nudges", 0)
        if nudges >= max_nudges:
            return
        ctx.messages.append(
            Message(role=Role.USER, content=[TextBlock(text=_NUDGE_TEXT)])
        )
        ctx.scratch["intent_nudges"] = nudges + 1
        ctx.scratch["should_resume"] = True

    return hook


def detect_intent_without_action(text: str) -> bool:
    """Exposed for unit tests."""
    return bool(_INTENT_PATTERN.search(text))


# --------------------------------------------------------------------------- #
# Final delivery guard.
# --------------------------------------------------------------------------- #

_ARTIFACT_CLAIM_PATTERN = re.compile(
    r"\b(?:created|wrote|modified|edited|fixed|updated|saved|implemented)\b|"
    r"already\s+(?:created|wrote|modified|edited|fixed|updated|saved|implemented)|"
    r"\u5df2(?:\u521b\u5efa|\u5199\u5165|\u5199\u597d|"
    r"\u4fee\u6539|\u4fee\u590d|\u66f4\u65b0|\u4fdd\u5b58|\u5b9e\u73b0)|"
    r"\u5df2\u7ecf(?:\u521b\u5efa|\u5199\u5165|\u5199\u597d|"
    r"\u4fee\u6539|\u4fee\u590d|\u66f4\u65b0|\u4fdd\u5b58|\u5b9e\u73b0)|"
    r"\u5199\u597d\u4e86|\u6539\u597d\u4e86|\u4fee\u597d\u4e86",
    re.IGNORECASE,
)
_COMMAND_CLAIM_PATTERN = re.compile(
    r"\b(?:ran|executed)\b|already\s+(?:ran|executed)|"
    r"\u5df2(?:\u8fd0\u884c|\u6267\u884c)|"
    r"\u5df2\u7ecf(?:\u8fd0\u884c|\u6267\u884c)|"
    r"\u8fd0\u884c\u4e86|\u6267\u884c\u4e86",
    re.IGNORECASE,
)
_VERIFY_CLAIM_PATTERN = re.compile(
    r"\b(?:tested|verified|checked)\b|already\s+(?:tested|verified|checked)|"
    r"\u5df2(?:\u6d4b\u8bd5|\u9a8c\u8bc1|\u68c0\u67e5)|"
    r"\u5df2\u7ecf(?:\u6d4b\u8bd5|\u9a8c\u8bc1|\u68c0\u67e5)|"
    r"\u6d4b\u8bd5\u4e86|\u9a8c\u8bc1\u4e86|\u68c0\u67e5\u4e86",
    re.IGNORECASE,
)
_BLOCKED_DELIVERY_PATTERN = re.compile(
    r"\b(?:cannot|can't|can not|unable|blocked|denied|failed|missing|required)\b|"
    r"\u65e0\u6cd5|\u4e0d\u80fd|\u4e0d\u5141\u8bb8|"
    r"\u88ab\u62d2\u7edd|\u5931\u8d25|\u7f3a\u5c11|\u9700\u8981",
    re.IGNORECASE,
)
_OUTPUT_PATH_PATTERN = re.compile(
    r"(?:save(?:\s+it)?\s+to|write(?:\s+it)?\s+to|"
    r"create(?:\s+it)?\s+(?:at|as|in)|output\s+to|"
    r"\u4fdd\u5b58(?:\u5230|\u4e3a)|"
    r"\u5199(?:\u5230|\u5165|\u8fdb)|"
    r"\u521b\u5efa(?:\u5230|\u4e3a)|"
    r"\u8f93\u51fa(?:\u5230|\u4e3a))"
    r"\s*[:\uff1a]?\s*`?([^\s`\uff0c\u3002\uff1b;]+)`?",
    re.IGNORECASE,
)
_BACKTICK_PATH_PATTERN = re.compile(
    r"`([^`\n]+\.(?:html|py|js|ts|tsx|jsx|css|md|txt|json|yaml|yml|"
    r"docx|xlsx|png|jpg|jpeg|gif|svg|pdf))`",
    re.IGNORECASE,
)

_FINAL_GUARD_PREFIX = "Delivery contract failed:"
_ACCEPTANCE_SUMMARY_PATTERN = re.compile(
    r"(?:Acceptance Summary|\u9a8c\u6536\u6458\u8981)",
    re.IGNORECASE,
)
_ACCEPTANCE_EDIT_TOOLS = {
    "write",
    "edit",
    "docxedit",
    "exceledit",
    "wordedit",
}


def _clean_path_text(value: str) -> str:
    return value.strip().strip("`'\".,;:)]}")


def _path_key(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return value


def _extract_requested_output_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in _OUTPUT_PATH_PATTERN.finditer(text or ""):
        path = _clean_path_text(match.group(1))
        if path:
            paths.append(path)
    for match in _BACKTICK_PATH_PATTERN.finditer(text or ""):
        prefix = text[max(0, match.start() - 40) : match.start()]
        if re.search(
            r"save|write|create|output|"
            r"\u4fdd\u5b58|\u5199|\u521b\u5efa|\u8f93\u51fa",
            prefix,
            re.IGNORECASE,
        ):
            path = _clean_path_text(match.group(1))
            if path:
                paths.append(path)
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        key = _path_key(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _first_user_text(ctx: LoopContext) -> str:
    for msg in ctx.messages:
        if msg.role != Role.USER:
            continue
        parts = [b.text for b in msg.content if isinstance(b, TextBlock)]
        if parts:
            return "\n".join(parts)
    return ""


def _successful_tools(ctx: LoopContext) -> set[str]:
    return set(ctx.scratch.get("successful_tool_names") or set())


def _path_has_write_evidence(ctx: LoopContext, path: str) -> bool:
    key = _path_key(path)
    written = set(ctx.scratch.get("written_files") or set())
    edited = set(ctx.scratch.get("edited_files") or set())
    if key in written or key in edited:
        return True
    # Bash can create files too. Accept it only when the target exists.
    if "bash" in _successful_tools(ctx):
        try:
            return Path(path).expanduser().exists()
        except Exception:
            return False
    return False


def _target_exists(path: str) -> bool:
    try:
        return Path(path).expanduser().exists()
    except Exception:
        return False


def _claimed_categories(text: str) -> set[str]:
    categories: set[str] = set()
    if _ARTIFACT_CLAIM_PATTERN.search(text):
        categories.add("artifact")
    if _COMMAND_CLAIM_PATTERN.search(text):
        categories.add("command")
    if _VERIFY_CLAIM_PATTERN.search(text):
        categories.add("verification")
    return categories


def _missing_delivery_evidence(ctx: LoopContext, assistant_text: str) -> list[str]:
    if _BLOCKED_DELIVERY_PATTERN.search(assistant_text):
        return []

    missing: list[str] = []
    successful = _successful_tools(ctx)
    categories = _claimed_categories(assistant_text)
    requested_paths = _extract_requested_output_paths(_first_user_text(ctx))

    for path in requested_paths:
        if not _path_has_write_evidence(ctx, path):
            state = (
                "does not exist"
                if not _target_exists(path)
                else "has no write/edit evidence"
            )
            missing.append(f"requested output path {path!r} {state}")

    if "artifact" in categories and not (
        successful & {"write", "edit", "docxedit", "exceledit", "wordedit", "bash"}
    ):
        missing.append("artifact claim has no successful write/edit tool evidence")
    if "command" in categories and "bash" not in successful:
        missing.append("command execution claim has no successful Bash evidence")
    if "verification" in categories and not (
        successful
        & {
            "bash",
            "read",
            "grep",
            "glob",
            "excelread",
            "wordread",
            "verify",
            "renderdocument",
            "render_pdf_page",
            "read_image",
        }
    ):
        missing.append("verification claim has no successful check evidence")

    return missing


def make_final_guard_hook(max_nudges: int = 2):
    """Return a StopHook that enforces tool-backed delivery claims.

    The hook catches a common failure mode: the assistant says it wrote,
    executed, tested, or verified something, but the loop has no matching tool
    evidence. It appends a corrective user message and resumes the loop.
    """

    async def hook(ctx: LoopContext) -> None:
        if not ctx.messages:
            return
        last = ctx.messages[-1]
        if last.role != Role.ASSISTANT:
            return
        if any(isinstance(b, ToolUseBlock) for b in last.content):
            return
        text = "".join(b.text for b in last.content if isinstance(b, TextBlock))
        if not text:
            return
        missing = _missing_delivery_evidence(ctx, text)
        if not missing:
            return

        nudges = ctx.scratch.get("final_guard_nudges", 0)
        if nudges >= max_nudges:
            return
        ctx.messages.append(
            Message(
                role=Role.USER,
                content=[
                    TextBlock(
                        text=(
                            f"{_FINAL_GUARD_PREFIX} {'; '.join(missing)}. "
                            "Execute the missing tool calls now, then verify and "
                            "answer. Do not ask the user whether to proceed."
                        )
                    )
                ],
            )
        )
        ctx.scratch["final_guard_nudges"] = nudges + 1
        ctx.scratch["should_resume"] = True

    return hook


def make_acceptance_summary_hook(max_nudges: int = 1):
    """Require explicit completion/non-completion summary after artifact edits."""

    async def hook(ctx: LoopContext) -> None:
        if not ctx.messages:
            return
        last = ctx.messages[-1]
        if last.role != Role.ASSISTANT:
            return
        if any(isinstance(b, ToolUseBlock) for b in last.content):
            return
        text = "".join(b.text for b in last.content if isinstance(b, TextBlock))
        if not text:
            return
        if _ACCEPTANCE_SUMMARY_PATTERN.search(text):
            return
        successful = _successful_tools(ctx)
        if not (successful & _ACCEPTANCE_EDIT_TOOLS):
            return

        nudges = ctx.scratch.get("acceptance_summary_nudges", 0)
        if nudges >= max_nudges:
            return
        ctx.messages.append(
            Message(
                role=Role.USER,
                content=[
                    TextBlock(
                        text=(
                            "Acceptance summary required. Reply with a concise "
                            "`\u9a8c\u6536\u6458\u8981` containing three parts: "
                            "Completed, Not completed/unsupported, and Evidence. "
                            "If a requested item was not actually changed or "
                            "verified, list it under Not completed/unsupported. "
                            "Only call more tools if you can close a concrete gap."
                        )
                    )
                ],
            )
        )
        ctx.scratch["acceptance_summary_nudges"] = nudges + 1
        ctx.scratch["should_resume"] = True

    return hook


# --------------------------------------------------------------------------- #
# PreToolUse approval: first-call confirmation for NEEDS_APPROVAL tools.
# --------------------------------------------------------------------------- #

Approver = Callable[[ToolUseBlock, LoopContext], Awaitable[bool]]


async def _auto_allow(_use: ToolUseBlock, _ctx: LoopContext) -> bool:
    return True


def make_approval_hook(
    tools: dict,
    approver: Optional[Approver] = None,
    *,
    remember: bool = True,
):
    """Return a PreToolUseHook that gates NEEDS_APPROVAL tools.

    - ``approver(use, ctx)`` is invoked on the first call of each gated tool.
      Return True to allow, False to deny.
    - ``remember=True`` (default) caches the approval in
      ``ctx.scratch['approved_tools']`` so subsequent calls of the same tool
      skip the approver.
    - SAFE tools always pass through without invoking the approver.
    - If ``approver`` is None, every NEEDS_APPROVAL call is auto-allowed.
      Wire a real prompter (UI modal, CLI confirm) to enforce gating.
    """
    appr = approver or _auto_allow

    async def hook(use: ToolUseBlock, ctx: LoopContext):
        tool = tools.get(use.name)
        if tool is None:
            return use
        if tool.permission_level == PermissionLevel.SAFE:
            return use
        approved = ctx.scratch.setdefault("approved_tools", set())
        if remember and use.name in approved:
            return use
        ok = await appr(use, ctx)
        if not ok:
            return ToolResultBlock(
                tool_use_id="",
                content=(
                    f"Tool {use.name!r} was denied by the user. "
                    "Suggest an alternative or ask for clarification."
                ),
                is_error=True,
            )
        if remember:
            approved.add(use.name)
        return use

    return hook


# ---------------------------------------------------------------------------
# Diff preview hook (P12.2): rich Accept/Reject card for textual mutations.
# ---------------------------------------------------------------------------


# Async callable; given the diff payload, return ``{"approved": bool}``.
DiffPreviewHandler = Callable[[dict], Awaitable[dict]]


def _read_file_safe(path: Path) -> tuple[str, bool]:
    """Return (current_text, existed). Missing/binary files map to ("", False)."""
    if not path.exists():
        return "", False
    try:
        return path.read_text(encoding="utf-8"), True
    except Exception:
        return "", True  # exists but unreadable as text


def _unified_diff(before: str, after: str, *, path: str, n_context: int = 3) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=n_context,
    )
    return "".join(diff)


def _resolve_path_against_workspace(raw: str, ctx: LoopContext) -> Optional[Path]:
    """Best-effort resolve a tool path argument to an absolute Path.

    Returns ``None`` if the path is empty/clearly invalid. Does not enforce
    the workspace boundary — the tool's own check will. We only need the
    real text to compute the diff.
    """
    if not raw:
        return None
    try:
        p = Path(str(raw)).expanduser()
        if not p.is_absolute():
            root = ctx.config.workspace_root
            if root is not None:
                p = Path(root) / p
        return p
    except Exception:
        return None


def build_write_diff(use: ToolUseBlock, ctx: LoopContext) -> Optional[dict]:
    """Build a diff payload for a Write tool call."""
    path_arg = str(use.input.get("path") or "")
    new_content = str(use.input.get("content") or "")
    resolved = _resolve_path_against_workspace(path_arg, ctx)
    if resolved is None:
        return None
    before, existed = _read_file_safe(resolved)
    unified = _unified_diff(before, new_content, path=path_arg)
    return {
        "tool": "Write",
        "path": path_arg,
        "exists": existed,
        "before_lines": len(before.splitlines()),
        "after_lines": len(new_content.splitlines()),
        "unified_diff": unified,
        # For very small files, also pass the full before/after so the UI
        # can render a side-by-side view if it wants.
        "before_text": before if len(before) <= 20_000 else None,
        "after_text": new_content if len(new_content) <= 20_000 else None,
    }


def build_edit_diff(use: ToolUseBlock, ctx: LoopContext) -> Optional[dict]:
    """Build a diff payload for an Edit tool call."""
    path_arg = str(use.input.get("path") or "")
    old = str(use.input.get("old_string") or "")
    new = str(use.input.get("new_string") or "")
    replace_all = bool(use.input.get("replace_all"))
    resolved = _resolve_path_against_workspace(path_arg, ctx)
    if resolved is None or not resolved.exists():
        return None
    before, _ = _read_file_safe(resolved)
    if old not in before:
        return None  # the tool itself will error out
    if replace_all:
        after = before.replace(old, new)
        occurrences = before.count(old)
    else:
        after = before.replace(old, new, 1)
        occurrences = 1
    unified = _unified_diff(before, after, path=path_arg)
    return {
        "tool": "Edit",
        "path": path_arg,
        "exists": True,
        "occurrences_changed": occurrences,
        "replace_all": replace_all,
        "before_lines": len(before.splitlines()),
        "after_lines": len(after.splitlines()),
        "unified_diff": unified,
        "before_text": before if len(before) <= 20_000 else None,
        "after_text": after if len(after) <= 20_000 else None,
    }


def _truncate(value: str, limit: int = 120) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _format_value_preview(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return _truncate(str(value), limit=120)
    try:
        import json as _json

        return _truncate(_json.dumps(value, ensure_ascii=False), limit=120)
    except Exception:
        return _truncate(repr(value), limit=120)


def _summarize_excel_op(raw: dict) -> Optional[dict]:
    """Render one ExcelRuntimeEdit op as a structured preview row.

    Returns ``None`` for read-only ops (``get_structure``).
    """
    op = str(raw.get("op") or "").strip()
    sheet = str(raw.get("sheet") or "").strip()
    cell = str(raw.get("cell") or "").strip()
    rng = str(raw.get("range") or "").strip()
    if op == "get_structure":
        return None
    if op == "set_cell":
        value = _format_value_preview(raw.get("value"))
        coord = f"{sheet}!{cell}" if sheet and cell else (sheet or cell)
        return {
            "op": op,
            "kind": "set_cell",
            "sheet": sheet,
            "cell": cell,
            "value": value,
            "summary": f"Set {coord} = {value}",
        }
    if op == "set_formula":
        formula = _truncate(str(raw.get("formula") or "").strip(), limit=180)
        coord = f"{sheet}!{cell}" if sheet and cell else (sheet or cell)
        return {
            "op": op,
            "kind": "set_formula",
            "sheet": sheet,
            "cell": cell,
            "formula": formula,
            "summary": f"Set formula {coord} = {formula}",
        }
    if op == "set_range_values":
        values = raw.get("values") or []
        rows = len(values) if isinstance(values, list) else 0
        cols = 0
        sample: list[str] = []
        if rows > 0 and isinstance(values[0], list):
            cols = len(values[0])
            for cell_value in values[0][:8]:
                sample.append(_format_value_preview(cell_value))
        coord = f"{sheet}!{rng}" if sheet and rng else (sheet or rng)
        return {
            "op": op,
            "kind": "set_range_values",
            "sheet": sheet,
            "range": rng,
            "rows": rows,
            "cols": cols,
            "sample_row": sample,
            "summary": (
                f"Write range {coord} ({rows}×{cols})"
                + (
                    f" — first row: {', '.join(sample) }"
                    if sample
                    else ""
                )
            ),
        }
    if op == "create_named_range":
        name = str(raw.get("name") or "").strip()
        refers_to = str(raw.get("refers_to") or "").strip()
        target = refers_to or (f"{sheet}!{rng}" if sheet and rng else (sheet or rng))
        return {
            "op": op,
            "kind": "create_named_range",
            "name": name,
            "target": target,
            "summary": f"Create named range {name} → {target}",
        }
    if op == "refresh_calculation":
        return {
            "op": op,
            "kind": "side_effect",
            "summary": "Recalculate workbook (formula refresh)",
        }
    return {
        "op": op or "(unknown)",
        "kind": "other",
        "summary": f"Excel op: {op}",
    }


def build_excel_runtime_diff(use: ToolUseBlock, ctx: LoopContext) -> Optional[dict]:
    """Structured preview for an ExcelRuntimeEdit call."""
    path_arg = str(use.input.get("path") or "")
    raw_ops = use.input.get("ops") or []
    if not isinstance(raw_ops, list) or not raw_ops:
        return None
    rows: list[dict] = []
    mutating = False
    for raw in raw_ops:
        if not isinstance(raw, dict):
            continue
        row = _summarize_excel_op(raw)
        if row is None:
            continue
        rows.append(row)
        # refresh_calculation is the one side_effect that still mutates the
        # workbook's stored values; other side_effects (none for now) would
        # not trigger a preview on their own.
        if row["kind"] != "side_effect" or row.get("op") == "refresh_calculation":
            mutating = True
    if not rows or not mutating:
        return None
    return {
        "tool": "ExcelRuntimeEdit",
        "path": path_arg,
        "op_count": len(rows),
        "op_summary": rows,
    }


def _bbox_from(raw: dict) -> Optional[list[float]]:
    fields = ("left", "top", "width", "height")
    if any(raw.get(field) is None for field in fields):
        return None
    try:
        return [float(raw[field]) for field in fields]
    except (TypeError, ValueError):
        return None


def _shape_style_fields(raw: dict) -> dict:
    fields = {}
    for key in ("fill_color", "line_color", "font_color", "font_size", "bold"):
        value = raw.get(key)
        if value is None or value == "":
            continue
        fields[key] = value
    return fields


def _summarize_powerpoint_op(raw: dict) -> Optional[dict]:
    """Render one PowerPointRuntimeEdit op as a structured preview row."""
    op = str(raw.get("op") or "").strip()
    slide = raw.get("slide")
    name = str(raw.get("name") or "").strip()
    if op in {"get_structure", "save"}:
        return None
    if op == "create_presentation":
        return {
            "op": op,
            "kind": "side_effect",
            "summary": "Create new presentation",
        }
    if op == "add_slide":
        layout = str(raw.get("layout") or "").strip()
        return {
            "op": op,
            "kind": "add_slide",
            "layout": layout or "default",
            "summary": f"Add slide (layout {layout or 'default'})",
        }
    if op == "add_text_box":
        bbox = _bbox_from(raw)
        text = _truncate(str(raw.get("text") or "").strip(), limit=120)
        return {
            "op": op,
            "kind": "add_text",
            "slide": slide,
            "text": text,
            "bbox": bbox,
            "summary": (
                f"Add text box on slide {slide}"
                + (f" @ ({bbox[0]:.0f},{bbox[1]:.0f},{bbox[2]:.0f}×{bbox[3]:.0f})" if bbox else "")
                + (f": {text}" if text else "")
            ),
        }
    if op == "add_shape":
        bbox = _bbox_from(raw)
        shape_type = str(raw.get("shape_type") or "").strip()
        return {
            "op": op,
            "kind": "add_shape",
            "slide": slide,
            "shape_type": shape_type,
            "bbox": bbox,
            "summary": (
                f"Add {shape_type or 'shape'} on slide {slide}"
                + (
                    f" @ ({bbox[0]:.0f},{bbox[1]:.0f},{bbox[2]:.0f}×{bbox[3]:.0f})"
                    if bbox
                    else ""
                )
            ),
        }
    if op == "add_connector":
        endpoints = None
        try:
            endpoints = {
                "from": [float(raw["x1"]), float(raw["y1"])],
                "to": [float(raw["x2"]), float(raw["y2"])],
            }
        except (KeyError, TypeError, ValueError):
            endpoints = None
        connector_type = str(raw.get("connector_type") or "").strip()
        return {
            "op": op,
            "kind": "add_connector",
            "slide": slide,
            "connector_type": connector_type or "straight",
            "endpoints": endpoints,
            "summary": (
                f"Add {connector_type or 'straight'} connector on slide {slide}"
                + (
                    f" — ({endpoints['from'][0]:.0f},{endpoints['from'][1]:.0f}) → "
                    f"({endpoints['to'][0]:.0f},{endpoints['to'][1]:.0f})"
                    if endpoints
                    else ""
                )
            ),
        }
    if op == "set_shape_style":
        fields = _shape_style_fields(raw)
        return {
            "op": op,
            "kind": "set_shape_style",
            "slide": slide,
            "name": name,
            "fields": fields,
            "summary": (
                f"Style {name or 'shape'} on slide {slide}"
                + (
                    " — "
                    + ", ".join(f"{k}={v}" for k, v in fields.items())
                    if fields
                    else ""
                )
            ),
        }
    if op == "set_shape_geometry":
        bbox = _bbox_from(raw)
        return {
            "op": op,
            "kind": "set_shape_geometry",
            "slide": slide,
            "name": name,
            "bbox": bbox,
            "summary": (
                f"Move/resize {name or 'shape'} on slide {slide}"
                + (
                    f" → ({bbox[0]:.0f},{bbox[1]:.0f},{bbox[2]:.0f}×{bbox[3]:.0f})"
                    if bbox
                    else ""
                )
            ),
        }
    return {
        "op": op or "(unknown)",
        "kind": "other",
        "summary": f"PowerPoint op: {op}",
    }


def build_powerpoint_runtime_diff(use: ToolUseBlock, ctx: LoopContext) -> Optional[dict]:
    """Structured preview for a PowerPointRuntimeEdit call."""
    path_arg = str(use.input.get("path") or "")
    raw_ops = use.input.get("ops") or []
    if not isinstance(raw_ops, list) or not raw_ops:
        return None
    rows: list[dict] = []
    mutating = False
    for raw in raw_ops:
        if not isinstance(raw, dict):
            continue
        row = _summarize_powerpoint_op(raw)
        if row is None:
            continue
        rows.append(row)
        if row["kind"] != "side_effect" or row.get("op") == "create_presentation":
            mutating = True
    if not rows or not mutating:
        return None
    return {
        "tool": "PowerPointRuntimeEdit",
        "path": path_arg,
        "op_count": len(rows),
        "op_summary": rows,
    }


def _summarize_word_op(raw: dict) -> Optional[dict]:
    """Render one WordRuntimeEdit op as a structured preview row.

    Returns ``None`` for read-only ops (no preview needed) so the hook
    can short-circuit when every requested op is read-only.
    """
    op = str(raw.get("op") or "").strip()
    anchor = str(raw.get("anchor_heading") or raw.get("anchor") or "").strip()
    new_text = str(raw.get("new_text") or raw.get("text") or "").strip()
    style = str(raw.get("style") or "").strip()
    if op == "get_structure":
        return None  # read-only
    if op == "set_heading_text":
        return {
            "op": op,
            "kind": "rename_heading",
            "before": anchor,
            "after": new_text,
            "summary": f"Rename heading: “{anchor}” → “{new_text}”",
        }
    if op == "insert_paragraph_after_heading":
        return {
            "op": op,
            "kind": "insert_after_heading",
            "anchor": anchor,
            "after_text": new_text,
            "style": style or "Normal",
            "summary": (
                f"Insert under “{anchor}” (style {style or 'Normal'}): "
                f"{(new_text[:120] + '…') if len(new_text) > 120 else new_text}"
            ),
        }
    if op == "refresh_fields":
        return {
            "op": op,
            "kind": "side_effect",
            "summary": "Refresh fields (TOC, page numbers, cross-refs)",
        }
    if op == "add_toc":
        levels = str(raw.get("levels") or "1-3").strip()
        title = str(raw.get("title") or "").strip()
        return {
            "op": op,
            "kind": "side_effect",
            "summary": (
                f"Add / refresh TOC (levels {levels}"
                + (f", title “{title}”" if title else "")
                + ")"
            ),
        }
    return {
        "op": op or "(unknown)",
        "kind": "other",
        "summary": f"Word op: {op}",
    }


def build_word_runtime_diff(use: ToolUseBlock, ctx: LoopContext) -> Optional[dict]:
    """Structured preview for a WordRuntimeEdit call.

    Each op becomes a row in ``op_summary``. Read-only requests
    (only ``get_structure``) return ``None`` so the hook does not surface
    a useless approval card.
    """
    path_arg = str(use.input.get("path") or "")
    raw_ops = use.input.get("ops") or []
    if not isinstance(raw_ops, list) or not raw_ops:
        return None
    rows: list[dict] = []
    mutating = False
    for raw in raw_ops:
        if not isinstance(raw, dict):
            continue
        row = _summarize_word_op(raw)
        if row is None:
            continue
        rows.append(row)
        if row["kind"] != "side_effect" or row.get("op") in {"refresh_fields", "add_toc"}:
            # add_toc and refresh_fields still mutate the doc — keep the preview.
            mutating = True
    if not rows or not mutating:
        return None
    return {
        "tool": "WordRuntimeEdit",
        "path": path_arg,
        "op_count": len(rows),
        "op_summary": rows,
    }


_DIFF_BUILDERS = {
    "Write": build_write_diff,
    "Edit": build_edit_diff,
    "WordRuntimeEdit": build_word_runtime_diff,
    "ExcelRuntimeEdit": build_excel_runtime_diff,
    "PowerPointRuntimeEdit": build_powerpoint_runtime_diff,
}


def _has_previewable_change(payload: Optional[dict]) -> bool:
    """A payload is worth surfacing if it carries a textual diff or op rows."""
    if not payload:
        return False
    if (payload.get("unified_diff") or "").strip():
        return True
    if payload.get("op_summary"):
        return True
    return False


def make_diff_preview_hook(
    tools: dict,
    handler: Optional[DiffPreviewHandler],
    *,
    enabled_tools: Optional[set[str]] = None,
):
    """Return a PreToolUseHook that shows a diff / op preview for mutation tools.

    Textual tools (Write/Edit) preview a unified diff; the *Runtime* tools
    (Word/Excel/PowerPoint) preview a structured ``op_summary``. On approval
    the original ``ToolUseBlock`` is returned unchanged AND the tool is added
    to both ``ctx.scratch["approved_tools"]`` (so the plain approval hook does
    not double-ask) and ``ctx.scratch["diff_preview_approved"]`` (so callers
    can tell the rich card was already shown). On rejection a
    ``ToolResultBlock(is_error=True)`` short-circuits the call.

    Without a handler (CLI / batch runs) the hook is a no-op.
    """
    targets = set(enabled_tools or _DIFF_BUILDERS.keys())

    async def hook(use: ToolUseBlock, ctx: LoopContext):
        if handler is None:
            return use
        if use.name not in targets:
            return use
        builder = _DIFF_BUILDERS.get(use.name)
        if builder is None:
            return use
        try:
            payload = builder(use, ctx)
        except Exception:
            return use  # builder failure: fall through to standard gating
        if not _has_previewable_change(payload):
            return use  # nothing meaningful to show; let the tool itself run/err
        try:
            reply = await handler({
                "tool_use_id": use.id,
                **payload,
            })
        except Exception as exc:
            return ToolResultBlock(
                tool_use_id="",
                content=f"Diff preview failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )
        if not isinstance(reply, dict) or not reply.get("approved"):
            return ToolResultBlock(
                tool_use_id="",
                content=(
                    "User rejected the proposed change. Do not retry the "
                    "same edit; propose an alternative or ask for guidance."
                ),
                is_error=True,
            )
        ctx.scratch.setdefault("approved_tools", set()).add(use.name)
        ctx.scratch.setdefault("diff_preview_approved", set()).add(use.name)
        return use

    return hook
