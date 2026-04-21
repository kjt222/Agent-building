"""Built-in hooks for the agent loop.

Provides: intent-without-action Stop hook, and the PreToolUse approval hook
used to gate NEEDS_APPROVAL tools (first-call confirmation).
"""

from __future__ import annotations

import re
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
# a tool_use. Mixed EN/ZH to match real agent traffic.
_INTENT_PATTERN = re.compile(
    r"(?:\bI['’]?ll\b|\bI will\b|\blet me\b|\bnext,? I\b|"
    r"\bgoing to\b|\bI['’]?m going to\b|"
    r"接下来我|下一步我|下一步可以|我来|直接帮你|马上就|"
    r"我现在来|我会先|让我来|我可以|如果你要|你回复|要我)",
    re.IGNORECASE,
)

_NUDGE_TEXT = (
    "You announced an action but did not call a tool. Execute it now by "
    "calling the appropriate tool. Do not describe what you will do; do it."
)


def make_intent_without_action_hook(max_nudges: int = 2):
    """Return a StopHook that nudges the model when it announces an action
    without emitting a tool_use.

    Reschedules by appending a user message to ctx.messages and setting a
    flag so the caller (AgentLoop.run) can reconsider termination.
    """

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
# PreToolUse approval — first-call confirmation for NEEDS_APPROVAL tools.
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
            return use  # let dispatcher emit its own "not found" error
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
