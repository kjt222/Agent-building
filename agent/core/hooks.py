"""Built-in hooks for the agent loop.

Provides:
- intent-without-action Stop hook;
- final delivery guard Stop hook;
- PreToolUse approval hook for NEEDS_APPROVAL tools.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Awaitable, Callable, Optional

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
