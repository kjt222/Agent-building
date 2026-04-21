"""tau2-bench adapter for our OpenAIAdapter / AgentLoop scaffold.

Plugs an `OurScaffoldAgent` into tau2's HalfDuplexAgent protocol. tau2 drives
the conversation synchronously (`generate_next_message`) and provides its own
Orchestrator for user simulation, tool execution, and scoring. We keep our
model plumbing (AsyncOpenAI streaming via OpenAIAdapter) but let tau2 own the
outer loop — this isolates "our scaffold's prompt + stream handling" as the
only variable vs the `llm_agent` baseline.

Per-turn flow:
  1. tau2 hands us a UserMessage or ToolMessage (or MultiToolMessage).
  2. We append to state.messages, then translate the entire
     system_messages + messages transcript into our internal Message/Block format.
  3. Run one `OpenAIAdapter.stream(...)` turn via `asyncio.run` (the sync
     worker thread has no running loop — tau2's batch runner calls us from a
     ThreadPoolExecutor).
  4. Accumulate streamed deltas into a tau2 AssistantMessage obeying the
     "content XOR tool_calls" validator.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import List, Optional

from pydantic import BaseModel

from tau2.agent.base.llm_config import LLMConfigMixin
from tau2.agent.base_agent import (
    HalfDuplexAgent,
    ValidAgentInputMessage,
    is_valid_agent_history_message,
)
from tau2.data_model.message import (
    APICompatibleMessage,
    AssistantMessage,
    Message as Tau2Message,
    MultiToolMessage,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool as Tau2Tool

from agent.core.loop import (
    Message as LoopMessage,
    Role,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    ToolUseDelta,
    TurnEnd,
)
from agent.models.openai_adapter_v2 import OpenAIAdapter
from agent.models.openai_responses_adapter import OpenAIResponsesAdapter

# --------------------------------------------------------------------------- #
# System prompt — mirrors the baseline LLMAgent prompt so the only variable
# vs baseline is *our* scaffold, not the instruction text.
# --------------------------------------------------------------------------- #

AGENT_INSTRUCTION = """
You are a customer service agent that helps the user according to the <policy> provided below.
In each turn you can either:
- Send a message to the user.
- Make a tool call.
You cannot do both at the same time.

Try to be helpful and always follow the policy. Always make sure you generate valid JSON only.
""".strip()

SYSTEM_PROMPT = """
<instructions>
{agent_instruction}
</instructions>
<policy>
{domain_policy}
</policy>
""".strip()


# --------------------------------------------------------------------------- #
# State (reuse LLMAgent's shape so any downstream eval code that introspects
# state.messages / state.system_messages works unchanged).
# --------------------------------------------------------------------------- #


class OurScaffoldState(BaseModel):
    system_messages: list[SystemMessage]
    messages: list[APICompatibleMessage]


# --------------------------------------------------------------------------- #
# Message conversion: tau2 transcript → our internal LoopMessage list
# --------------------------------------------------------------------------- #


def _tau2_to_internal(
    messages: list[APICompatibleMessage],
) -> list[LoopMessage]:
    """Convert a flat tau2 transcript into internal LoopMessage blocks.

    tau2's transcript is a flat list (system/user/assistant/tool) where tool
    messages stand alone. Our internal format groups tool_result blocks as
    content of a user-role Message. For streaming straight into our adapter we
    don't need grouping — we can emit one internal Message per tau2 message
    with a single block, because `_internal_to_openai` handles both
    "user w/ text blocks" and "user w/ tool_result blocks only" paths.
    """
    out: list[LoopMessage] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            # System prompt is passed separately via `system=` parameter.
            continue

        if isinstance(m, UserMessage):
            out.append(
                LoopMessage(
                    role=Role.USER,
                    content=[TextBlock(text=m.content or "")],
                )
            )

        elif isinstance(m, AssistantMessage):
            blocks = []
            if m.content:
                blocks.append(TextBlock(text=m.content))
            if m.tool_calls:
                for tc in m.tool_calls:
                    blocks.append(
                        ToolUseBlock(id=tc.id, name=tc.name, input=dict(tc.arguments))
                    )
            if not blocks:
                # Defensive: an assistant turn with neither content nor tool
                # calls shouldn't exist, but don't emit an empty message
                # (OpenAI rejects role=assistant w/ null content+no tool_calls).
                blocks.append(TextBlock(text=""))
            out.append(LoopMessage(role=Role.ASSISTANT, content=blocks))

        elif isinstance(m, ToolMessage):
            out.append(
                LoopMessage(
                    role=Role.USER,
                    content=[
                        ToolResultBlock(
                            tool_use_id=m.id,
                            content=m.content or "",
                            is_error=bool(m.error),
                        )
                    ],
                )
            )
    return out


def _tau2_tools_to_internal(tools: list[Tau2Tool]) -> list[dict]:
    """Extract name/description/schema from tau2 Tool → our internal shape."""
    out = []
    for t in tools:
        schema = t.openai_schema["function"]
        out.append(
            {
                "name": schema["name"],
                "description": schema.get("description", ""),
                "input_schema": schema.get("parameters", {}),
            }
        )
    return out


# --------------------------------------------------------------------------- #
# The agent
# --------------------------------------------------------------------------- #


class OurScaffoldAgent(LLMConfigMixin, HalfDuplexAgent[OurScaffoldState]):
    """A half-duplex tau2 agent that drives our OpenAIAdapter streaming path."""

    def __init__(
        self,
        tools: List[Tau2Tool],
        domain_policy: str,
        llm: str,
        llm_args: Optional[dict] = None,
    ):
        super().__init__(
            tools=tools,
            domain_policy=domain_policy,
            llm=llm,
            llm_args=llm_args,
        )
        # Pre-compute internal tool schemas (stable across turns).
        self._internal_tools = _tau2_tools_to_internal(tools)
        # LLMConfigMixin deepcopies llm_args into self.llm_args. Pop `provider`
        # from the copy so it doesn't leak into the API call via **self.llm_args,
        # which would cause a 400 from both OpenAI and DashScope.
        provider = self.llm_args.pop("provider", "openai")
        # Qwen's Responses endpoint is the only tool-capable path we expose for
        # DashScope; OpenAI uses Responses only when reasoning_effort is set.
        if provider == "qwen":
            self._adapter = OpenAIResponsesAdapter(model=llm, provider="qwen")
        else:
            use_responses = bool(
                llm_args and llm_args.get("reasoning_effort") not in (None, "none")
            )
            if use_responses:
                self._adapter = OpenAIResponsesAdapter(
                    model=llm,
                    api_key=os.getenv("OPENAI_API_KEY"),
                    base_url=os.getenv("OPENAI_BASE_URL"),
                    provider="openai",
                )
            else:
                self._adapter = OpenAIAdapter(
                    model=llm,
                    api_key=os.getenv("OPENAI_API_KEY"),
                    base_url=os.getenv("OPENAI_BASE_URL"),
                )

    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(
            agent_instruction=AGENT_INSTRUCTION,
            domain_policy=self.domain_policy,
        )

    def get_init_state(
        self, message_history: Optional[list[Tau2Message]] = None
    ) -> OurScaffoldState:
        if message_history is None:
            message_history = []
        assert all(is_valid_agent_history_message(m) for m in message_history), (
            "Message history must contain only AssistantMessage, UserMessage, or ToolMessage."
        )
        return OurScaffoldState(
            system_messages=[SystemMessage(role="system", content=self.system_prompt)],
            messages=message_history,
        )

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: OurScaffoldState
    ) -> tuple[AssistantMessage, OurScaffoldState]:
        if isinstance(message, UserMessage) and message.is_audio:
            raise ValueError("Audio messages are not supported by this agent.")

        # Append incoming (one ToolMessage or UserMessage per turn; batches
        # arrive as MultiToolMessage).
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        assistant_message = self._run_one_turn(state)
        state.messages.append(assistant_message)
        return assistant_message, state

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _run_one_turn(self, state: OurScaffoldState) -> AssistantMessage:
        internal_msgs = _tau2_to_internal(state.messages)
        text_parts: list[str] = []
        tool_uses: dict[str, ToolUseBlock] = {}
        stop_reason = "end_turn"

        async def _drive() -> None:
            nonlocal stop_reason
            async for delta in self._adapter.stream(
                messages=internal_msgs,
                tools=self._internal_tools,
                system=self.system_prompt,
                **(self.llm_args or {}),
            ):
                if isinstance(delta, TextDelta):
                    text_parts.append(delta.text)
                elif isinstance(delta, ToolUseDelta):
                    existing = tool_uses.get(delta.id)
                    if existing is None:
                        tool_uses[delta.id] = ToolUseBlock(
                            id=delta.id, name=delta.name, input=dict(delta.input_partial)
                        )
                    else:
                        existing.input.update(delta.input_partial)
                elif isinstance(delta, TurnEnd):
                    stop_reason = delta.stop_reason

        asyncio.run(_drive())

        # Build tau2 AssistantMessage obeying the content-XOR-tool_calls rule.
        if tool_uses:
            tcs = [
                ToolCall(
                    id=b.id or f"call_{uuid.uuid4().hex[:12]}",
                    name=b.name,
                    arguments=b.input,
                    requestor="assistant",
                )
                for b in tool_uses.values()
            ]
            return AssistantMessage.text(content=None, tool_calls=tcs)

        text = "".join(text_parts).strip()
        if not text:
            # Neither content nor tool calls would trip tau2's validator.
            # Emit a benign stall message — orchestrator will detect.
            text = "(empty turn)"
        return AssistantMessage.text(content=text)


# --------------------------------------------------------------------------- #
# Factory — registered under the name "our_scaffold"
# --------------------------------------------------------------------------- #


def create_our_agent(tools, domain_policy, **kwargs):
    """Factory for the eval framework. Mirrors `create_llm_agent` signature."""
    return OurScaffoldAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm"),
        llm_args=kwargs.get("llm_args"),
    )
