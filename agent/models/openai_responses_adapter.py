"""OpenAI Responses API adapter — parallel to openai_adapter_v2 (chat.completions).

Reason for existence: gpt-5 series with function tools + reasoning_effort is
only supported on /v1/responses. chat.completions returns:
    "Function tools with reasoning_effort are not supported for gpt-5.4
    in /v1/chat/completions. Please use /v1/responses instead."

Speaks the same ModelAdapter protocol as openai_adapter_v2, so AgentLoop and
every benchmark adapter can swap between them transparently.

Stateless: each stream() call sends the full transcript as Responses API
`input` items (role messages + function_call + function_call_output).
Between turns we drop reasoning items — they're opaque & encrypted for stateless
callers anyway. Within each turn reasoning_effort still fires, which is what
buys us the headline lift. If we later want cross-turn reasoning continuity,
switch to previous_response_id mode.

Streaming event surface we care about:
- response.output_text.delta      → TextDelta
- response.reasoning_summary.delta → ReasoningDelta
- response.function_call_arguments.delta → args buffer per call_id
- response.output_item.added (type=function_call) → seed name + call_id
- response.completed              → TurnEnd
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from agent.core.loop import (
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


# --------------------------------------------------------------------------- #
# Internal → Responses API input translation.
# --------------------------------------------------------------------------- #


def _internal_to_responses_input(
    messages: list[Message],
) -> list[dict]:
    """Flatten our internal Message list into Responses API `input` items.

    Responses API input items:
      - {"role": "user" | "assistant" | "system", "content": "..."}   (text)
      - {"type": "function_call", "call_id": ..., "name": ..., "arguments": "json"}
      - {"type": "function_call_output", "call_id": ..., "output": "..."}
    """
    out: list[dict] = []
    for msg in messages:
        if msg.role == Role.USER:
            tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
            text_blocks = [b for b in msg.content if isinstance(b, TextBlock)]
            image_blocks = [b for b in msg.content if isinstance(b, ImageBlock)]
            for b in tool_results:
                content = (
                    b.content
                    if isinstance(b.content, str)
                    else json.dumps(b.content, ensure_ascii=False)
                )
                out.append(
                    {
                        "type": "function_call_output",
                        "call_id": b.tool_use_id,
                        "output": content,
                    }
                )
            if image_blocks and not tool_results:
                content: list[dict] = []
                text = "".join(b.text for b in text_blocks)
                if text:
                    content.append({"type": "input_text", "text": text})
                for b in image_blocks:
                    content.append({
                        "type": "input_image",
                        "image_url": f"data:{b.media_type};base64,{b.base64}",
                    })
                out.append({"role": "user", "content": content})
            elif text_blocks and not tool_results:
                out.append(
                    {
                        "role": "user",
                        "content": "".join(b.text for b in text_blocks),
                    }
                )
            elif text_blocks:
                out.append(
                    {
                        "role": "user",
                        "content": "".join(b.text for b in text_blocks),
                    }
                )
        elif msg.role == Role.ASSISTANT:
            text_blocks = [b for b in msg.content if isinstance(b, TextBlock)]
            tool_uses = [b for b in msg.content if isinstance(b, ToolUseBlock)]
            text = "".join(b.text for b in text_blocks).strip()
            if text:
                out.append({"role": "assistant", "content": text})
            for b in tool_uses:
                out.append(
                    {
                        "type": "function_call",
                        "call_id": b.id,
                        "name": b.name,
                        "arguments": json.dumps(b.input, ensure_ascii=False),
                    }
                )
        elif msg.role == Role.SYSTEM:
            out.append(
                {
                    "role": "system",
                    "content": "".join(
                        b.text for b in msg.content if isinstance(b, TextBlock)
                    ),
                }
            )
    return out


def _internal_tools_to_responses(tools: list[dict]) -> list[dict]:
    """Internal tool schema → Responses API function tool schema.

    Note: flatter than chat.completions — no nested `function:` wrapper.
    """
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }
        for t in tools
    ]


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #


class OpenAIResponsesAdapter:
    """ModelAdapter talking to /v1/responses. Drop-in for OpenAIAdapter."""

    name = "openai-responses"

    # Provider presets: (env_var_for_key, default_base_url).
    _PROVIDER_PRESETS = {
        "openai": ("OPENAI_API_KEY", None),
        "qwen": (
            "DASHSCOPE_API_KEY",
            "https://dashscope.aliyuncs.com/api/v2/apps/protocols/compatible-mode/v1",
        ),
    }

    def __init__(
        self,
        model: str = "gpt-5.4",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: str = "openai",
    ):
        if provider not in self._PROVIDER_PRESETS:
            raise ValueError(f"unknown provider {provider!r}")
        self.provider = provider
        env_name, default_base = self._PROVIDER_PRESETS[provider]
        key = api_key or os.getenv(env_name)
        if not key:
            raise RuntimeError(f"{env_name} not set")
        self.client = AsyncOpenAI(
            api_key=key,
            base_url=base_url or default_base,
        )
        self.model = model

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        system: Optional[str] = None,
        **options,
    ) -> AsyncIterator[Delta]:
        input_items = _internal_to_responses_input(messages)
        api_tools = _internal_tools_to_responses(tools) if tools else None

        kwargs: dict = {
            "model": self.model,
            "input": input_items,
            "stream": True,
        }
        if system:
            kwargs["instructions"] = system
        if api_tools:
            kwargs["tools"] = api_tools

        # reasoning_effort comes in as flat kwarg (from tau2's llm_args_agent).
        # OpenAI: translate to reasoning={"effort": "..."}.
        # Qwen:   translate to extra_body={"enable_thinking": True} — qwen3.5+
        #         only supports the boolean toggle, not graded effort.
        reasoning_effort = options.pop("reasoning_effort", None)
        if reasoning_effort is not None and reasoning_effort != "none":
            if self.provider == "qwen":
                kwargs.setdefault("extra_body", {})["enable_thinking"] = True
            else:
                kwargs["reasoning"] = {"effort": reasoning_effort}

        # Drop kwargs the Responses API doesn't accept. `seed` is the common
        # one (tau2's runner injects it; chat.completions accepted it but
        # responses.create rejects it). Reasoning models also don't take
        # `temperature`/`top_p` with effort>=low — silently drop to avoid
        # 400s; callers that truly need sampling control should pass them
        # only to the chat.completions adapter.
        for bad in ("seed", "temperature", "top_p"):
            options.pop(bad, None)
        kwargs.update(options)

        # Per-call_id function arg buffers (streamed as fragments).
        # Responses streams: output_item.added gives us name+call_id first,
        # then function_call_arguments.delta events for that item_id.
        call_seed: dict[str, dict] = {}  # item_id -> {"id", "name", "args_buf"}
        final_stop = "end_turn"
        final_usage: dict = {}

        stream = await self.client.responses.create(**kwargs)
        async for event in stream:
            etype = getattr(event, "type", "")

            if etype == "response.output_text.delta":
                txt = getattr(event, "delta", None)
                if txt:
                    yield TextDelta(text=txt)

            elif etype in (
                "response.reasoning.delta",
                "response.reasoning_summary.delta",
                "response.reasoning_summary_text.delta",
            ):
                txt = getattr(event, "delta", None)
                if txt:
                    yield ReasoningDelta(text=txt)

            elif etype == "response.output_item.added":
                item = getattr(event, "item", None)
                if item is not None and getattr(item, "type", "") == "function_call":
                    item_id = getattr(item, "id", None) or getattr(event, "item_id", None)
                    call_seed[item_id] = {
                        "id": getattr(item, "call_id", None),
                        "name": getattr(item, "name", None),
                        "args_buf": [],
                    }

            elif etype == "response.function_call_arguments.delta":
                item_id = getattr(event, "item_id", None)
                slot = call_seed.get(item_id)
                if slot is None:
                    slot = call_seed.setdefault(
                        item_id, {"id": None, "name": None, "args_buf": []}
                    )
                frag = getattr(event, "delta", None)
                if frag:
                    slot["args_buf"].append(frag)

            elif etype == "response.function_call_arguments.done":
                # Args finalized for this call; name/call_id may arrive here too
                item_id = getattr(event, "item_id", None)
                slot = call_seed.get(item_id)
                if slot is not None:
                    full_args = getattr(event, "arguments", None)
                    if full_args and not slot["args_buf"]:
                        slot["args_buf"] = [full_args]

            elif etype == "response.completed":
                resp = getattr(event, "response", None)
                if resp is not None:
                    # Walk final output items to catch any function_call we
                    # didn't stream incrementally (happens on non-stream paths
                    # or when the server batches). Merge into call_seed.
                    for item in getattr(resp, "output", []) or []:
                        if getattr(item, "type", "") == "function_call":
                            iid = getattr(item, "id", None)
                            slot = call_seed.setdefault(
                                iid,
                                {"id": None, "name": None, "args_buf": []},
                            )
                            if not slot["id"]:
                                slot["id"] = getattr(item, "call_id", None)
                            if not slot["name"]:
                                slot["name"] = getattr(item, "name", None)
                            if not slot["args_buf"]:
                                args = getattr(item, "arguments", "") or ""
                                if args:
                                    slot["args_buf"] = [args]
                    # Stop reason: if any function_call present → tool_use.
                    has_fc = any(
                        getattr(it, "type", "") == "function_call"
                        for it in getattr(resp, "output", []) or []
                    )
                    final_stop = "tool_use" if has_fc else "end_turn"
                    # Usage. Responses naming: input_tokens / output_tokens /
                    # reasoning_tokens nested under output_tokens_details.
                    usage = getattr(resp, "usage", None)
                    if usage is not None:
                        final_usage = {
                            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                            "reasoning_tokens": getattr(
                                getattr(usage, "output_tokens_details", None),
                                "reasoning_tokens",
                                0,
                            ) or 0,
                            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                        }

            elif etype == "response.failed" or etype == "error":
                err = getattr(event, "error", None) or getattr(event, "response", None)
                raise RuntimeError(f"Responses stream error: {err}")

        # Emit one ToolUseDelta per completed function_call.
        for slot in call_seed.values():
            if not slot["id"] or not slot["name"]:
                continue
            raw = "".join(slot["args_buf"]) or "{}"
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"_raw_arguments": raw}
            yield ToolUseDelta(id=slot["id"], name=slot["name"], input_partial=parsed)

        yield TurnEnd(stop_reason=final_stop, usage=final_usage)
