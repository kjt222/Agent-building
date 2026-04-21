"""Loop control tools: plan mode, subagent delegation.

These tools reach into LoopContext (not pure functions). Kept in a separate
module from primitives so it's obvious they mutate run-scoped state.
"""

from __future__ import annotations

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
        ctx.scratch["plan_exited"] = True
        ctx.scratch["plan_text"] = plan
        return ToolResultBlock(
            tool_use_id="",
            content="Plan recorded. Write/exec tools are now unlocked.",
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
