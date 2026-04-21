"""OpenAI ModelAdapter — speaks the provider-neutral Message/Delta protocol
defined in agent/core/loop.py.

Decoupled from the legacy `openai_adapter.py` so the old chat-completions call
sites keep working during migration.
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from agent.core.loop import (
    BlockType,
    Delta,
    ImageBlock,
    Message,
    ReasoningDelta,
    Role,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    ToolUseDelta,
    TurnEnd,
)


def _internal_to_openai(
    messages: list[Message], system: Optional[str]
) -> list[dict]:
    out: list[dict] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        if msg.role == Role.USER:
            tool_result_blocks = [b for b in msg.content if isinstance(b, ToolResultBlock)]
            text_blocks = [b for b in msg.content if isinstance(b, TextBlock)]
            image_blocks = [b for b in msg.content if isinstance(b, ImageBlock)]
            if tool_result_blocks:
                for b in tool_result_blocks:
                    content = b.content if isinstance(b.content, str) else json.dumps(b.content, ensure_ascii=False)
                    out.append({
                        "role": "tool",
                        "tool_call_id": b.tool_use_id,
                        "content": content,
                    })
                if text_blocks:
                    out.append({
                        "role": "user",
                        "content": "".join(b.text for b in text_blocks),
                    })
            else:
                if image_blocks:
                    content: list[dict] = []
                    text = "".join(b.text for b in text_blocks)
                    if text:
                        content.append({"type": "text", "text": text})
                    for b in image_blocks:
                        content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{b.media_type};base64,{b.base64}"
                            },
                        })
                    out.append({"role": "user", "content": content})
                    continue
                out.append({
                    "role": "user",
                    "content": "".join(b.text for b in text_blocks),
                })
        elif msg.role == Role.ASSISTANT:
            text_blocks = [b for b in msg.content if isinstance(b, TextBlock)]
            tool_uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
            entry: dict = {
                "role": "assistant",
                "content": "".join(b.text for b in text_blocks) or None,
            }
            if tool_uses:
                entry["tool_calls"] = [
                    {
                        "id": b.id,
                        "type": "function",
                        "function": {
                            "name": b.name,
                            "arguments": json.dumps(b.input, ensure_ascii=False),
                        },
                    }
                    for b in tool_uses
                ]
            out.append(entry)
        elif msg.role == Role.SYSTEM:
            out.append({"role": "system", "content": "".join(
                b.text for b in msg.content if isinstance(b, TextBlock)
            )})
    return out


def _internal_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Internal tool schema → OpenAI function schema."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


class OpenAIAdapter:
    name = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self.client = AsyncOpenAI(api_key=key, base_url=base_url)
        self.model = model

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        system: Optional[str] = None,
        **options,
    ) -> AsyncIterator[Delta]:
        oai_messages = _internal_to_openai(messages, system)
        oai_tools = _internal_tools_to_openai(tools) if tools else None

        kwargs: dict = {
            "model": self.model,
            "messages": oai_messages,
            "stream": True,
            # Emit a final chunk with usage data (no choices) after the content
            # chunks. Harmless on providers that ignore unknown fields.
            "stream_options": {"include_usage": True},
        }
        if oai_tools:
            kwargs["tools"] = oai_tools
        kwargs.update(options)

        # Accumulators for streamed tool_calls. OpenAI sends tool_calls by
        # index; name/id arrive in the first chunk, arguments arrive as JSON
        # string fragments across many chunks.
        tool_call_acc: dict[int, dict] = {}  # index -> {id, name, args_buf}
        final_stop = "end_turn"
        final_usage: dict = {}

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            # The usage chunk arrives at end of stream with empty choices.
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                final_usage = {
                    "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "reasoning_tokens": getattr(
                        getattr(usage, "completion_tokens_details", None),
                        "reasoning_tokens",
                        0,
                    ) or 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                }
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta

            if getattr(delta, "content", None):
                yield TextDelta(text=delta.content)

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                yield ReasoningDelta(text=reasoning)

            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index
                    slot = tool_call_acc.setdefault(
                        idx, {"id": None, "name": None, "args_buf": []}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args_buf"].append(tc.function.arguments)

            finish = choice.finish_reason
            if finish:
                if finish == "tool_calls":
                    final_stop = "tool_use"
                elif finish == "length":
                    final_stop = "max_tokens"
                else:
                    final_stop = "end_turn"

        for slot in tool_call_acc.values():
            if not slot["id"] or not slot["name"]:
                continue
            raw = "".join(slot["args_buf"]) or "{}"
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"_raw_arguments": raw}
            yield ToolUseDelta(id=slot["id"], name=slot["name"], input_partial=parsed)

        yield TurnEnd(stop_reason=final_stop, usage=final_usage)
