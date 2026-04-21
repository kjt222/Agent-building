"""Additional AgentLoop v2 adapters for non-OpenAI providers."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Optional

from agent.core.loop import (
    Delta,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    ToolUseDelta,
    TurnEnd,
)
from agent.models.http_utils import request_json


def _text_from_blocks(msg: Message) -> str:
    return "".join(b.text for b in msg.content if isinstance(b, TextBlock))


class AnthropicAgentLoopAdapter:
    """Minimal Anthropic Messages adapter for AgentLoop.

    It uses the non-streaming Messages API and emits normalized deltas after
    the response arrives. That keeps provider switching functional while the
    loop remains provider-neutral.
    """

    name = "anthropic"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")

    def _messages(self, messages: list[Message]) -> list[dict]:
        out: list[dict] = []
        for msg in messages:
            if msg.role == Role.SYSTEM:
                continue
            if msg.role == Role.ASSISTANT:
                content: list[dict] = []
                text = _text_from_blocks(msg)
                if text:
                    content.append({"type": "text", "text": text})
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
                continue

            content = []
            text = _text_from_blocks(msg)
            if text:
                content.append({"type": "text", "text": text})
            for block in msg.content:
                if isinstance(block, ImageBlock):
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": block.media_type,
                            "data": block.base64,
                        },
                    })
                elif isinstance(block, ToolResultBlock):
                    content.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": (
                            block.content
                            if isinstance(block.content, str)
                            else json.dumps(block.content, ensure_ascii=False)
                        ),
                        "is_error": block.is_error,
                    })
            out.append({"role": "user", "content": content or [{"type": "text", "text": ""}]})
        return out

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        system: Optional[str] = None,
        **options: Any,
    ) -> AsyncIterator[Delta]:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(options.pop("max_tokens", 4096) or 4096),
            "messages": self._messages(messages),
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("input_schema") or {"type": "object"},
                }
                for t in tools
            ]
        for key in ("temperature", "top_p", "stop_sequences"):
            if options.get(key) is not None:
                payload[key] = options[key]

        data = await asyncio.to_thread(
            request_json,
            "POST",
            f"{self.base_url}/messages",
            self.api_key,
            payload,
            120.0,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )

        stop_reason = "tool_use" if data.get("stop_reason") == "tool_use" else "end_turn"
        usage = data.get("usage") or {}
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                yield TextDelta(text=str(block["text"]))
            elif block.get("type") == "tool_use":
                yield ToolUseDelta(
                    id=str(block.get("id") or ""),
                    name=str(block.get("name") or ""),
                    input_partial=block.get("input") or {},
                )
        yield TurnEnd(
            stop_reason=stop_reason,
            usage={
                "input_tokens": usage.get("input_tokens", 0) or 0,
                "output_tokens": usage.get("output_tokens", 0) or 0,
                "total_tokens": (usage.get("input_tokens", 0) or 0)
                + (usage.get("output_tokens", 0) or 0),
            },
        )


class GeminiAgentLoopAdapter:
    """Gemini adapter for AgentLoop v2.

    The google-generativeai SDK is synchronous here; calls run in a thread and
    are normalized to AgentLoop deltas. Tool declarations are passed through as
    Gemini function declarations when supported by the SDK.
    """

    name = "gemini"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: Optional[str] = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    def _parts(self, messages: list[Message]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = "assistant" if msg.role == Role.ASSISTANT else "user"
            text = _text_from_blocks(msg)
            if text:
                lines.append(f"{role}: {text}")
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    content = block.content if isinstance(block.content, str) else json.dumps(block.content, ensure_ascii=False)
                    lines.append(f"tool_result({block.tool_use_id}): {content}")
                elif isinstance(block, ToolUseBlock):
                    lines.append(f"assistant_tool_use({block.name}): {json.dumps(block.input, ensure_ascii=False)}")
        return "\n".join(lines)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        system: Optional[str] = None,
        **options: Any,
    ) -> AsyncIterator[Delta]:
        def _call():
            try:
                import google.generativeai as genai
            except ImportError as exc:
                raise RuntimeError(
                    "Google Generative AI SDK not installed. Install `google-generativeai`."
                ) from exc
            genai.configure(api_key=self.api_key)
            tool_payload = None
            if tools:
                tool_payload = [{
                    "function_declarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": t.get("input_schema") or {"type": "object"},
                        }
                        for t in tools
                    ]
                }]
            model = genai.GenerativeModel(
                self.model,
                system_instruction=system,
                tools=tool_payload,
            )
            return model.generate_content(self._parts(messages))

        response = await asyncio.to_thread(_call)
        stop_reason = "end_turn"
        yielded = False
        try:
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    function_call = getattr(part, "function_call", None)
                    if function_call and getattr(function_call, "name", None):
                        args = dict(getattr(function_call, "args", {}) or {})
                        yield ToolUseDelta(
                            id=f"gemini_{function_call.name}",
                            name=str(function_call.name),
                            input_partial=args,
                        )
                        stop_reason = "tool_use"
                        yielded = True
                    elif getattr(part, "text", None):
                        yield TextDelta(text=str(part.text))
                        yielded = True
        except Exception:
            text = getattr(response, "text", "")
            if text:
                yield TextDelta(text=str(text))
                yielded = True
        if not yielded:
            text = getattr(response, "text", "")
            if text:
                yield TextDelta(text=str(text))
        yield TurnEnd(stop_reason=stop_reason, usage={})
