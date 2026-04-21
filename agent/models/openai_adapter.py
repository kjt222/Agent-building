from __future__ import annotations

from typing import Any

from .base import ModelAdapter, ModelCapabilities
from .http_utils import request_json, stream_json


class OpenAIAdapter(ModelAdapter):
    capabilities = ModelCapabilities(chat=True, embeddings=True, tools=True, json_mode=True, multimodal=True)

    def _base_url(self) -> str:
        return str(self.extra.get("base_url") or "https://api.openai.com/v1").rstrip("/")

    def _require_key(self) -> str:
        if not self.api_key:
            raise RuntimeError("OpenAI API key missing. Set api_key_env or api_key.")
        return self.api_key

    def chat(self, prompt: str, **kwargs: Any) -> str:
        model = kwargs.get("model", self.model)
        system_prompt = kwargs.get("system_prompt")
        messages = kwargs.get("messages")
        if messages is None:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
        payload = {"model": model, "messages": messages}
        for key in ("temperature", "top_p", "max_tokens", "stop", "response_format"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs.get(key)
        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        url = f"{self._base_url()}/chat/completions"
        data = request_json("POST", url, self._require_key(), payload=payload)
        choices = data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if content is None:
            content = choices[0].get("text", "")
        return content or ""

    def embed(self, text: str, **kwargs: Any) -> list[float]:
        model = kwargs.get("model", self.model)
        payload = {"model": model, "input": text}
        url = f"{self._base_url()}/embeddings"
        data = request_json("POST", url, self._require_key(), payload=payload)
        items = data.get("data") or []
        if not items:
            raise RuntimeError("Embedding response missing data.")
        embedding = items[0].get("embedding")
        if embedding is None:
            raise RuntimeError("Embedding response missing vector.")
        return embedding

    def chat_stream(self, prompt: str, **kwargs: Any):
        """流式聊天"""
        model = kwargs.get("model", self.model)
        system_prompt = kwargs.get("system_prompt")
        messages = kwargs.get("messages")
        if messages is None:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
        payload = {"model": model, "messages": messages, "stream": True}
        for key in ("temperature", "top_p", "max_tokens", "stop", "response_format"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs.get(key)
        extra_body = kwargs.get("extra_body")
        if isinstance(extra_body, dict):
            payload.update(extra_body)
        url = f"{self._base_url()}/chat/completions"

        for chunk in stream_json(url, self._require_key(), payload=payload, timeout=120.0):
            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta") or {}
            content = delta.get("content") or ""
            if content:
                yield {"type": "content", "text": content}

    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        **kwargs: Any
    ) -> dict:
        """
        调用模型并支持工具调用

        Args:
            messages: 消息列表
            tools: 工具定义列表 (OpenAI格式)

        Returns:
            dict: {
                "content": str,
                "reasoning": str | None,
                "tool_calls": list | None
            }
        """
        model = kwargs.get("model", self.model)
        payload = {"model": model, "messages": messages}

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        for key in ("temperature", "top_p", "max_tokens", "stop"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs.get(key)

        url = f"{self._base_url()}/chat/completions"
        data = request_json("POST", url, self._require_key(), payload=payload, timeout=120.0)

        choices = data.get("choices", [])
        if not choices:
            return {"content": "", "reasoning": None, "tool_calls": None}

        message = choices[0].get("message") or {}
        return {
            "content": message.get("content") or "",
            "reasoning": None,  # OpenAI 标准 API 不返回 reasoning
            "tool_calls": message.get("tool_calls")
        }

    def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        **kwargs: Any
    ):
        """
        流式调用模型并支持工具调用

        Yields:
            dict: 流式事件
                - {"type": "content", "text": "..."}
                - {"type": "tool_calls", "data": [...]}
        """
        import json as _json
        from pathlib import Path
        from datetime import datetime

        # 调试日志
        debug_log = Path(__file__).parent.parent.parent / "openai_debug.log"

        def log_debug(label: str, data: Any):
            try:
                with open(debug_log, "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"[{datetime.now().isoformat()}] {label}\n")
                    f.write(f"{'='*60}\n")
                    f.write(_json.dumps(data, ensure_ascii=False, indent=2, default=str))
                    f.write("\n")
            except:
                pass

        model = kwargs.get("model", self.model)
        payload = {"model": model, "messages": messages, "stream": True}

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        # 记录请求
        log_debug("REQUEST - payload keys", list(payload.keys()))
        log_debug("REQUEST - tools count", len(tools) if tools else 0)
        if tools:
            log_debug("REQUEST - tool names", [t.get("function", {}).get("name") for t in tools])

        for key in ("temperature", "top_p", "max_tokens", "stop"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs.get(key)

        url = f"{self._base_url()}/chat/completions"

        # 收集tool_calls（流式中分片返回）
        tool_calls_buffer: dict[int, dict] = {}
        chunk_count = 0

        for chunk in stream_json(url, self._require_key(), payload=payload, timeout=120.0):
            chunk_count += 1
            choices = chunk.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta") or {}

            # 记录前几个chunk用于调试
            if chunk_count <= 3:
                log_debug(f"CHUNK #{chunk_count} - delta keys", list(delta.keys()) if delta else "empty")

            # 处理回答内容
            content = delta.get("content") or ""
            if content:
                yield {"type": "content", "text": content}

            # 处理工具调用（流式中分片返回）
            if delta.get("tool_calls"):
                log_debug("TOOL_CALLS detected in delta", delta.get("tool_calls"))
                for tc in delta["tool_calls"]:
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {"name": "", "arguments": ""}
                        }

                    if tc.get("id"):
                        tool_calls_buffer[idx]["id"] = tc["id"]
                    if tc.get("function"):
                        func = tc["function"]
                        if func.get("name"):
                            tool_calls_buffer[idx]["function"]["name"] = func["name"]
                        if func.get("arguments"):
                            tool_calls_buffer[idx]["function"]["arguments"] += func["arguments"]

        # 如果有工具调用，最后返回完整的tool_calls
        log_debug("FINAL - tool_calls_buffer", tool_calls_buffer)
        log_debug("FINAL - total chunks", chunk_count)

        if tool_calls_buffer:
            tool_calls = [tool_calls_buffer[i] for i in sorted(tool_calls_buffer.keys())]
            log_debug("FINAL - yielding tool_calls", tool_calls)
            yield {"type": "tool_calls", "data": tool_calls}
