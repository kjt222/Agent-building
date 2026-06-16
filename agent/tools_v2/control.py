"""Loop control tools: plan mode, subagent delegation.

These tools reach into LoopContext (not pure functions). Kept in a separate
module from primitives so it's obvious they mutate run-scoped state.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Optional, Protocol

from agent.core.loop import (
    AgentLoop,
    LoopConfig,
    LoopContext,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


# --------------------------------------------------------------------------- #
# ExitPlanMode — flips plan mode off via ctx.scratch flag.
# --------------------------------------------------------------------------- #


class ExitPlanModeTool:
    name = "exit_plan_mode"
    description = (
        "Call once you've finished read-only investigation and have a plan. "
        "Pass a one-paragraph plan in `plan`. This unlocks write/exec tools "
        "for the rest of the run."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "One-paragraph plan describing what you'll change and why.",
            }
        },
        "required": ["plan"],
    }
    permission_level = PermissionLevel.SAFE  # reading a plan is not a mutation
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        plan = (input.get("plan") or "").strip()
        if not plan:
            return ToolResultBlock(
                tool_use_id="", content="`plan` must be non-empty.", is_error=True
            )

        handler = ctx.scratch.get("plan_approval_handler")
        if not callable(handler):
            # Legacy / non-UI runs (CLI, unit, batch): no human reviewer, so
            # auto-approve and unlock immediately.
            ctx.scratch["plan_exited"] = True
            ctx.scratch["plan_text"] = plan
            return ToolResultBlock(
                tool_use_id="",
                content="Plan recorded. Write/exec tools are now unlocked.",
            )

        plan_id = uuid.uuid4().hex
        payload = {
            "plan_id": plan_id,
            "plan": plan,
            "conversation_id": ctx.scratch.get("conversation_id"),
        }
        try:
            reply = await handler(payload)
        except Exception as exc:
            return ToolResultBlock(
                tool_use_id="",
                content=f"Plan approval failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )
        if not isinstance(reply, dict):
            return ToolResultBlock(
                tool_use_id="",
                content=(
                    "Plan approval handler returned no decision; staying in "
                    "plan mode."
                ),
                is_error=True,
            )

        approved = bool(reply.get("approved"))
        note = str(reply.get("revision_note") or "").strip()
        history = ctx.scratch.setdefault("plan_history", [])
        history.append({
            "plan_id": plan_id,
            "plan": plan,
            "approved": approved,
            "revision_note": note,
        })
        if approved:
            ctx.scratch["plan_exited"] = True
            ctx.scratch["plan_approved"] = True
            ctx.scratch["plan_text"] = plan
            msg = "Plan approved. Write/exec tools are now unlocked."
            if note:
                msg += f" Reviewer note: {note}"
            return ToolResultBlock(tool_use_id="", content=msg)
        msg = (
            "Plan rejected; staying in plan mode. Revise and call "
            "exit_plan_mode again."
        )
        if note:
            msg += f" Reviewer note: {note}"
        return ToolResultBlock(tool_use_id="", content=msg, is_error=True)


# --------------------------------------------------------------------------- #
# AskUserQuestion — ask the user a clarifying question and block on the reply.
# Restored from the session transcript (P12.3) after the D-drive-format
# recovery reverted control.py to its pre-feature 4/23 version.
# --------------------------------------------------------------------------- #


class AskUserQuestionTool:
    """Ask the user a clarifying question and wait for their answer.

    The tool is SAFE — it does not mutate state. It is the correct first move
    when a user's request is genuinely ambiguous: file location, output
    format, target audience, naming, scope. Don't use it for trivial defaults
    (whitespace, casing, etc.) — pick a sensible default instead.

    The run is wired by the UI server: when the tool is called, the server
    emits an ``activity{type=user_question_request}`` SSE event with the
    question payload, registers a Future, and the tool blocks until the
    user POSTs an answer to ``/api/user_questions/{question_id}``.
    """

    name = "AskUserQuestion"
    description = (
        "Ask the user 1-2 clarifying questions before doing complex or "
        "ambiguous work. Use only when you genuinely don't know a load-bearing "
        "detail (output location, format, target audience, naming, scope). Do "
        "NOT ask trivial defaults. Pass at most 4 short options when the "
        "answer is a clear pick; otherwise leave options empty and accept "
        "free-form text. Returns the user's reply as JSON."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "One concise question (max ~2 sentences).",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Optional 2-4 short choice labels. If empty, the UI shows "
                    "a free-form text input only."
                ),
                "maxItems": 4,
            },
            "multi_select": {
                "type": "boolean",
                "description": "If true, the user may pick more than one option.",
                "default": False,
            },
            "context": {
                "type": "string",
                "description": "Optional short context (~1 sentence) shown above the question.",
            },
        },
        "required": ["question"],
    }
    permission_level = PermissionLevel.SAFE  # reading user input is not a mutation
    parallel_safe = False  # only one open question at a time

    def __init__(self, default_timeout_s: float = 600.0):
        self._default_timeout_s = float(default_timeout_s)

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        question = (input.get("question") or "").strip()
        if not question:
            return ToolResultBlock(
                tool_use_id="", content="`question` must be non-empty.", is_error=True
            )
        options_raw = input.get("options") or []
        if not isinstance(options_raw, list):
            return ToolResultBlock(
                tool_use_id="", content="`options` must be a list of strings.", is_error=True
            )
        options = [str(o).strip() for o in options_raw if str(o).strip()]
        if len(options) > 4:
            return ToolResultBlock(
                tool_use_id="",
                content="At most 4 options are allowed; trim the question.",
                is_error=True,
            )

        handler = ctx.scratch.get("user_question_handler")
        if not callable(handler):
            # Default behavior outside a UI server: no human in the loop. The
            # model gets a clear error so it knows to fall back to defaults.
            return ToolResultBlock(
                tool_use_id="",
                content=(
                    "No interactive user available in this run; pick a "
                    "sensible default and proceed without asking."
                ),
                is_error=True,
            )

        payload = {
            "question_id": uuid.uuid4().hex,
            "question": question,
            "options": options,
            "multi_select": bool(input.get("multi_select")),
            "context": (input.get("context") or "").strip(),
            "timeout_s": self._default_timeout_s,
        }
        try:
            reply = await handler(payload)
        except asyncio.TimeoutError:
            return ToolResultBlock(
                tool_use_id="",
                content=(
                    "User did not answer in time. Proceed with a sensible "
                    "default and explain the assumption in your final reply."
                ),
                is_error=True,
            )
        except Exception as exc:
            return ToolResultBlock(
                tool_use_id="",
                content=f"User question failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        # Record the question/answer for trace + UI replay.
        history = ctx.scratch.setdefault("user_questions", [])
        history.append({"request": payload, "reply": reply})
        return ToolResultBlock(
            tool_use_id="",
            content=json.dumps(
                {
                    "type": "user_question_reply",
                    "answer": str(reply.get("answer") or ""),
                    "selected_option": reply.get("selected_option"),
                    "selected_options": reply.get("selected_options"),
                },
                ensure_ascii=False,
            ),
        )


# --------------------------------------------------------------------------- #
# AgentTool — spawn a nested AgentLoop with a restricted toolset.
# --------------------------------------------------------------------------- #


class _ToolProto(Protocol):
    name: str
    description: str
    input_schema: dict
    permission_level: PermissionLevel
    parallel_safe: bool


class AgentTool:
    """Launch a subagent — a nested AgentLoop — for a well-scoped task.

    Typical use: investigation ('find how X is wired'), one-shot edits,
    parallel questions where keeping results out of the parent transcript
    saves tokens. The subagent runs to completion; only its final assistant
    text is returned as the tool result.
    """

    name = "Agent"
    description = (
        "Spawn a subagent to perform a focused task. The subagent has its own "
        "conversation context (not shared with you) and a restricted toolset. "
        "Use for: open-ended search/investigation, well-scoped edits that "
        "would clutter your transcript, or parallel questions. "
        "Returns the subagent's final summary."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Short (3-6 word) label for the task — for the user's UI.",
            },
            "prompt": {
                "type": "string",
                "description": "The full task for the subagent, including any context it needs.",
            },
            "subagent_type": {
                "type": "string",
                "description": (
                    "Preset name selecting the subagent's adapter/toolset/prompt. "
                    "If omitted, uses the default preset."
                ),
            },
        },
        "required": ["description", "prompt"],
    }
    permission_level = PermissionLevel.SAFE  # the subagent's own tools still gate
    parallel_safe = True

    def __init__(
        self,
        presets: "dict[str, SubagentPreset]",
        default_preset: str = "default",
    ):
        if default_preset not in presets:
            raise ValueError(f"default_preset {default_preset!r} missing from presets")
        self._presets = presets
        self._default = default_preset

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        preset_name = (input.get("subagent_type") or self._default).strip()
        preset = self._presets.get(preset_name)
        if preset is None:
            return ToolResultBlock(
                tool_use_id="",
                content=(
                    f"Unknown subagent_type {preset_name!r}. Available: "
                    f"{sorted(self._presets)}"
                ),
                is_error=True,
            )

        prompt = input.get("prompt", "")
        if not prompt.strip():
            return ToolResultBlock(
                tool_use_id="", content="`prompt` must be non-empty.", is_error=True
            )

        subloop = AgentLoop(
            adapter=preset.adapter,
            tools=preset.tools,
            config=LoopConfig(
                max_iterations=preset.max_iterations,
                system_prompt=preset.system_prompt,
                parallel_tool_calls=True,
                # subagent inherits plan state? No — each subagent starts fresh.
                permission_mode="default",
                trace_path=None,  # parent trace doesn't include subagent detail
            ),
        )

        final_text_parts: list[str] = []
        sub_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }

        last_assistant: Optional[Message] = None
        async for event in subloop.run(prompt):
            if isinstance(event, Message) and event.role == Role.ASSISTANT:
                last_assistant = event

        if last_assistant is not None:
            for b in last_assistant.content:
                if isinstance(b, TextBlock):
                    final_text_parts.append(b.text)

        # Propagate subagent usage into parent context so cost accounting is correct.
        # We can't access the subagent's ctx directly from here, but we can read
        # from LoopContext via a well-known scratch key if we stash it; simpler:
        # expose a method on AgentLoop to return its last ctx usage. For now,
        # accept some under-reporting — the next pass can wire it through by
        # letting AgentLoop return its final context.
        _ = sub_usage

        text = "".join(final_text_parts).strip() or "(subagent returned no text)"
        return ToolResultBlock(tool_use_id="", content=text)


# --------------------------------------------------------------------------- #
# Subagent preset: bundles an adapter + tool subset + a system prompt.
# --------------------------------------------------------------------------- #


class SubagentPreset:
    __slots__ = ("adapter", "tools", "system_prompt", "max_iterations", "description")

    def __init__(
        self,
        *,
        adapter,
        tools: dict[str, _ToolProto],
        system_prompt: str,
        max_iterations: int = 30,
        description: str = "",
    ):
        self.adapter = adapter
        self.tools = tools
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.description = description
