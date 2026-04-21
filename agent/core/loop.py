"""Agent main loop (Phase 1 skeleton).

Single-loop tool-use agent, modelled after Claude Code's architecture but
provider-agnostic. The loop speaks an internal message format; provider
adapters translate to/from OpenAI / Anthropic / Gemini / DeepSeek wire
formats.

This module is the skeleton. Concrete adapter bindings, hook dispatch, and
permission gating are wired in Phases 2-4.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Literal, Optional, Protocol


# ---------------------------------------------------------------------------
# Internal message format (provider-neutral)
# ---------------------------------------------------------------------------

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class BlockType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"


@dataclass
class TextBlock:
    text: str
    type: BlockType = BlockType.TEXT


@dataclass
class ImageBlock:
    base64: str
    media_type: str = "image/png"
    name: str = ""
    type: BlockType = BlockType.IMAGE


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: BlockType = BlockType.TOOL_USE


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str | list
    is_error: bool = False
    type: BlockType = BlockType.TOOL_RESULT


Block = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock


@dataclass
class Message:
    role: Role
    content: list[Block]


# ---------------------------------------------------------------------------
# Streaming deltas from provider adapters
# ---------------------------------------------------------------------------

@dataclass
class TextDelta:
    text: str


@dataclass
class ToolUseDelta:
    id: str
    name: str
    input_partial: dict  # may accumulate across deltas


@dataclass
class ReasoningDelta:
    text: str


@dataclass
class TurnEnd:
    stop_reason: str  # "end_turn" | "tool_use" | "max_tokens" | ...
    usage: dict = field(default_factory=dict)


Delta = TextDelta | ToolUseDelta | ReasoningDelta | TurnEnd


@dataclass
class _TurnComplete:
    message: "Message"
    stop_reason: str
    usage: dict


# ---------------------------------------------------------------------------
# Provider adapter protocol (each provider implements this)
# ---------------------------------------------------------------------------

class ModelAdapter(Protocol):
    name: str

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict],  # JSON Schema tool definitions (internal format)
        system: Optional[str] = None,
        **options,
    ) -> AsyncIterator[Delta]:
        """Stream a single model turn, yielding normalized deltas."""
        ...


# ---------------------------------------------------------------------------
# Tool protocol (normalized; replaces legacy tools/base.Tool in Phase 2)
# ---------------------------------------------------------------------------

class PermissionLevel(str, Enum):
    SAFE = "safe"
    NEEDS_APPROVAL = "needs_approval"
    DANGEROUS = "dangerous"


class ToolProtocol(Protocol):
    name: str
    description: str
    input_schema: dict
    permission_level: PermissionLevel
    parallel_safe: bool

    async def run(self, input: dict, ctx: "LoopContext") -> ToolResultBlock:
        ...


# ---------------------------------------------------------------------------
# Hooks (stubs for Phase 3/4)
# ---------------------------------------------------------------------------

PreToolUseHook = Callable[
    [ToolUseBlock, "LoopContext"],
    Awaitable[Optional["ToolUseBlock | ToolResultBlock"]],
]
PostToolUseHook = Callable[[ToolUseBlock, ToolResultBlock, "LoopContext"], Awaitable[ToolResultBlock]]
StopHook = Callable[["LoopContext"], Awaitable[None]]


@dataclass
class Hooks:
    pre_tool_use: list[PreToolUseHook] = field(default_factory=list)
    post_tool_use: list[PostToolUseHook] = field(default_factory=list)
    on_stop: list[StopHook] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loop configuration and context
# ---------------------------------------------------------------------------

@dataclass
class LoopConfig:
    max_iterations: int = 0  # 0 = unlimited (Claude Code default)
    parallel_tool_calls: bool = True
    system_prompt: Optional[str] = None
    # "plan" gates NEEDS_APPROVAL tools — see _run_one_tool.
    permission_mode: Literal["default", "plan"] = "default"
    # JSONL trace of each turn when set. Parent dirs created on first write.
    trace_path: Optional[Path] = None


@dataclass
class LoopContext:
    """Shared state for hooks and tools within a single run()."""
    config: LoopConfig
    messages: list[Message] = field(default_factory=list)
    iteration: int = 0
    scratch: dict = field(default_factory=dict)
    # Cumulative token usage across all turns in this run.
    usage: dict = field(default_factory=lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    })

    def add_usage(self, delta: dict) -> None:
        for k in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
            self.usage[k] = self.usage.get(k, 0) + (delta.get(k) or 0)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

class AgentLoop:
    def __init__(
        self,
        adapter: ModelAdapter,
        tools: dict[str, ToolProtocol],
        hooks: Optional[Hooks] = None,
        config: Optional[LoopConfig] = None,
    ):
        self.adapter = adapter
        self.tools = tools
        self.hooks = hooks or Hooks()
        self.config = config or LoopConfig()

    async def run(
        self,
        user_message: str,
        history: Optional[list[Message]] = None,
        images: Optional[list[ImageBlock | dict]] = None,
    ) -> AsyncIterator[Delta | Message]:
        """Run the loop, yielding streaming deltas and final messages.

        Caller consumes:
          - TextDelta / ReasoningDelta — stream to UI as tokens arrive
          - ToolUseDelta — optional; show 'calling tool X' status
          - Message (role=ASSISTANT) — completed assistant turn (persist)
          - Message (role=USER with tool_result blocks) — tool results appended
          - TurnEnd — per-turn end; loop may continue if stop_reason=tool_use
        """
        ctx = LoopContext(config=self.config)
        ctx.messages = list(history or [])
        user_content: list[Block] = [TextBlock(text=user_message)]
        for img in images or []:
            if isinstance(img, ImageBlock):
                user_content.append(img)
                continue
            base64_data = str(img.get("base64") or "").strip()
            if not base64_data:
                continue
            user_content.append(ImageBlock(
                base64=base64_data,
                media_type=str(img.get("media_type") or "image/png"),
                name=str(img.get("name") or ""),
            ))
        ctx.messages.append(Message(role=Role.USER, content=user_content))

        while True:
            ctx.iteration += 1
            if self.config.max_iterations and ctx.iteration > self.config.max_iterations:
                break

            turn_t0 = time.time()
            assistant_msg: Message | None = None
            stop_reason = "end_turn"
            turn_usage: dict = {}
            async for item in self._one_turn(ctx):
                if isinstance(item, _TurnComplete):
                    assistant_msg = item.message
                    stop_reason = item.stop_reason
                    turn_usage = item.usage
                else:
                    yield item
            if assistant_msg is None:
                assistant_msg = Message(role=Role.ASSISTANT, content=[])
            ctx.add_usage(turn_usage)
            ctx.messages.append(assistant_msg)
            yield assistant_msg

            tool_uses = [b for b in assistant_msg.content if isinstance(b, ToolUseBlock)]
            tool_results: list[ToolResultBlock] = []
            if tool_uses and stop_reason == "tool_use":
                tool_results = await self._dispatch_tools(tool_uses, ctx)
                tool_result_msg = Message(role=Role.USER, content=list(tool_results))
                ctx.messages.append(tool_result_msg)
                yield tool_result_msg
                self._write_trace(ctx, assistant_msg, tool_uses, tool_results,
                                  turn_usage, stop_reason, time.time() - turn_t0)
                continue

            self._write_trace(ctx, assistant_msg, tool_uses, tool_results,
                              turn_usage, stop_reason, time.time() - turn_t0)

            # No tool_use this turn. Run on_stop hooks; a hook may set
            # ctx.scratch['should_resume']=True (e.g. intent-without-action nudge)
            # to force another turn.
            ctx.scratch["should_resume"] = False
            for hook in self.hooks.on_stop:
                await hook(ctx)
            if ctx.scratch.get("should_resume"):
                # A hook appended a user message; yield it so caller sees it,
                # then loop.
                if ctx.messages and ctx.messages[-1].role == Role.USER:
                    yield ctx.messages[-1]
                continue
            break

    async def _one_turn(self, ctx: LoopContext) -> AsyncIterator[Delta | _TurnComplete]:
        """Run one model turn; accumulate streamed deltas into a Message.

        Yields provider deltas as they arrive, then a private completion object
        with the accumulated assistant message and turn metadata.
        """
        tool_schemas = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self.tools.values()
        ]

        text_buf: list[str] = []
        tool_uses: dict[str, ToolUseBlock] = {}
        stop_reason = "end_turn"
        turn_usage: dict = {}

        async for delta in self.adapter.stream(
            messages=ctx.messages,
            tools=tool_schemas,
            system=self.config.system_prompt,
        ):
            if isinstance(delta, TextDelta):
                text_buf.append(delta.text)
                yield delta
            elif isinstance(delta, ToolUseDelta):
                block = tool_uses.get(delta.id)
                if block is None:
                    block = ToolUseBlock(id=delta.id, name=delta.name, input={})
                    tool_uses[delta.id] = block
                block.input.update(delta.input_partial)
                yield delta
            elif isinstance(delta, ReasoningDelta):
                yield delta
            elif isinstance(delta, TurnEnd):
                stop_reason = delta.stop_reason
                turn_usage = delta.usage or {}
                yield delta

        content: list[Block] = []
        if text_buf:
            content.append(TextBlock(text="".join(text_buf)))
        content.extend(tool_uses.values())
        yield _TurnComplete(
            message=Message(role=Role.ASSISTANT, content=content),
            stop_reason=stop_reason,
            usage=turn_usage,
        )

    def _write_trace(
        self,
        ctx: LoopContext,
        assistant_msg: Message,
        tool_uses: list[ToolUseBlock],
        tool_results: list[ToolResultBlock],
        usage: dict,
        stop_reason: str,
        elapsed_s: float,
    ) -> None:
        """Append one JSONL record per turn if trace_path is set.

        Keeps tool inputs full (small) and tool result content truncated to
        400 chars (can get huge for shell output or file reads).
        """
        if not self.config.trace_path:
            return
        path = Path(self.config.trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        def _preview(s) -> str:
            text = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
            return text if len(text) <= 400 else text[:400] + "...<truncated>"

        text_parts = [b.text for b in assistant_msg.content if isinstance(b, TextBlock)]
        results_by_id = {r.tool_use_id: r for r in tool_results}
        system_prompt = self.config.system_prompt or ""
        record = {
            "iteration": ctx.iteration,
            "stop_reason": stop_reason,
            "system_prompt_hash": hashlib.sha256(
                system_prompt.encode("utf-8")
            ).hexdigest(),
            "assistant_text": _preview("".join(text_parts)),
            "tool_calls": [
                {
                    "id": u.id,
                    "name": u.name,
                    "input": u.input,
                    "result": _preview(
                        getattr(results_by_id.get(u.id), "content", "") or ""
                    ),
                    "is_error": getattr(results_by_id.get(u.id), "is_error", False),
                }
                for u in tool_uses
            ],
            "usage": usage,
            "elapsed_s": round(elapsed_s, 3),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def _dispatch_tools(
        self,
        tool_uses: list[ToolUseBlock],
        ctx: LoopContext,
    ) -> list[ToolResultBlock]:
        """Dispatch tool_use blocks; parallel when all are parallel_safe.

        PreToolUse hooks may: (a) return a rewritten ``ToolUseBlock`` to let
        the tool run, (b) return a ``ToolResultBlock`` to short-circuit with
        that result (e.g. a denial), or (c) return ``None`` to deny with a
        generic error. Hooks run in order; the first short-circuit wins.
        """
        # Each slot is either a ToolUseBlock (to run) or a pre-baked
        # ToolResultBlock (hook short-circuited).
        slots: list[ToolUseBlock | ToolResultBlock] = []
        for use in tool_uses:
            current: ToolUseBlock | ToolResultBlock = use
            for hook in self.hooks.pre_tool_use:
                result = await hook(current, ctx)
                if isinstance(result, ToolResultBlock):
                    result.tool_use_id = use.id
                    current = result
                    break
                if result is None:
                    current = ToolResultBlock(
                        tool_use_id=use.id,
                        content=f"Tool {use.name!r} was denied by a pre-tool-use hook.",
                        is_error=True,
                    )
                    break
                current = result  # rewritten ToolUseBlock; keep looping
            slots.append(current)

        runnable = [s for s in slots if isinstance(s, ToolUseBlock)]
        all_parallel = self.config.parallel_tool_calls and all(
            self.tools.get(u.name) and self.tools[u.name].parallel_safe for u in runnable
        )

        async def _resolve(s: ToolUseBlock | ToolResultBlock) -> ToolResultBlock:
            if isinstance(s, ToolResultBlock):
                return s
            return await self._run_one_tool(s, ctx)

        if all_parallel:
            results = await asyncio.gather(*(_resolve(s) for s in slots))
        else:
            results = []
            for s in slots:
                results.append(await _resolve(s))
        return list(results)

    async def _run_one_tool(
        self, use: ToolUseBlock, ctx: LoopContext
    ) -> ToolResultBlock:
        tool = self.tools.get(use.name)
        if tool is None:
            return ToolResultBlock(
                tool_use_id=use.id,
                content=f"Tool {use.name!r} not found.",
                is_error=True,
            )
        # Plan-mode gate: block tools that mutate state until the model
        # explicitly exits plan mode via ExitPlanMode.
        plan_active = (
            ctx.config.permission_mode == "plan"
            and not ctx.scratch.get("plan_exited")
        )
        if (
            plan_active
            and tool.permission_level != PermissionLevel.SAFE
            and tool.name != "exit_plan_mode"
        ):
            return ToolResultBlock(
                tool_use_id=use.id,
                content=(
                    f"Tool {tool.name!r} is blocked in plan mode (it can modify "
                    "state). Finish investigating with read-only tools, then "
                    "call `exit_plan_mode` with a one-paragraph plan; after "
                    "approval the loop will unlock write tools and you can "
                    "retry this call."
                ),
                is_error=True,
            )
        try:
            result = await tool.run(use.input, ctx)
        except Exception as exc:
            result = ToolResultBlock(
                tool_use_id=use.id, content=f"{type(exc).__name__}: {exc}", is_error=True
            )
        # Tools may leave tool_use_id blank; the loop owns binding it to the call.
        result.tool_use_id = use.id
        for hook in self.hooks.post_tool_use:
            result = await hook(use, result, ctx)
        self._record_tool_evidence(use, result, ctx)
        return result

    def _record_tool_evidence(
        self, use: ToolUseBlock, result: ToolResultBlock, ctx: LoopContext
    ) -> None:
        """Record tool evidence for delivery guards and debugging.

        The model's final answer is not trusted as proof that work happened.
        Stop hooks can inspect these sets to confirm that claimed writes,
        edits, commands, or verification had a corresponding tool call.
        """

        def _preview(value) -> str:
            text = value if isinstance(value, str) else json.dumps(
                value, ensure_ascii=False
            )
            return text if len(text) <= 400 else text[:400] + "...<truncated>"

        evidence = ctx.scratch.setdefault("tool_evidence", [])
        evidence.append({
            "tool": use.name,
            "input": dict(use.input or {}),
            "is_error": bool(result.is_error),
            "content": _preview(result.content),
        })

        if result.is_error:
            return

        tool_name = use.name.lower()
        ctx.scratch.setdefault("successful_tool_names", set()).add(tool_name)

        path_value = (use.input or {}).get("path") or (use.input or {}).get("file_path")
        if path_value:
            try:
                path_key = str(Path(str(path_value)).resolve())
            except Exception:
                path_key = str(path_value)
            if tool_name == "read":
                ctx.scratch.setdefault("read_files", set()).add(path_key)
            elif tool_name == "write":
                ctx.scratch.setdefault("written_files", set()).add(path_key)
            elif tool_name in {"edit", "docxedit"}:
                ctx.scratch.setdefault("edited_files", set()).add(path_key)

        if tool_name == "bash":
            command = str((use.input or {}).get("command") or "")
            if command:
                ctx.scratch.setdefault("ran_commands", []).append(command)
