from __future__ import annotations

from typing import Any, Iterable, List

from .base import ModelAdapter, ModelCapabilities


class FallbackAdapter(ModelAdapter):
    def __init__(self, adapters: Iterable[ModelAdapter]) -> None:
        self.adapters: List[ModelAdapter] = list(adapters)
        if not self.adapters:
            raise ValueError("FallbackAdapter requires at least one adapter")
        first = self.adapters[0]
        super().__init__(provider="fallback", model=first.model)
        self.capabilities = ModelCapabilities(
            chat=any(a.capabilities.chat for a in self.adapters),
            embeddings=any(a.capabilities.embeddings for a in self.adapters),
            tools=any(a.capabilities.tools for a in self.adapters),
            json_mode=any(a.capabilities.json_mode for a in self.adapters),
        )

    def _try(self, method: str, *args: Any, **kwargs: Any):
        errors = []
        for adapter in self.adapters:
            if method == "chat" and not adapter.capabilities.chat:
                continue
            if method == "embed" and not adapter.capabilities.embeddings:
                continue
            try:
                return getattr(adapter, method)(*args, **kwargs)
            except Exception as exc:  # pragma: no cover - network errors vary
                errors.append(f"{adapter.provider}: {exc}")
        raise RuntimeError("All providers failed: " + "; ".join(errors))

    def chat(self, prompt: str, **kwargs: Any) -> str:
        return self._try("chat", prompt, **kwargs)

    def embed(self, text: str, **kwargs: Any) -> list[float]:
        return self._try("embed", text, **kwargs)

    def chat_stream(self, prompt: str, **kwargs: Any):
        errors = []
        for adapter in self.adapters:
            if not adapter.capabilities.chat:
                continue
            stream_fn = getattr(adapter, "chat_stream", None)
            if stream_fn is None:
                try:
                    yield adapter.chat(prompt, **kwargs)
                    return
                except Exception as exc:
                    errors.append(f"{adapter.provider}: {exc}")
                    continue
            try:
                for chunk in stream_fn(prompt, **kwargs):
                    yield chunk
                return
            except Exception as exc:
                errors.append(f"{adapter.provider}: {exc}")
                continue
        raise RuntimeError("All providers failed: " + "; ".join(errors))

    def chat_with_tools(self, messages: list, tools: list, **kwargs: Any) -> dict:
        """调用模型并支持工具调用"""
        errors = []
        for adapter in self.adapters:
            if not adapter.capabilities.tools:
                continue
            fn = getattr(adapter, "chat_with_tools", None)
            if fn is None:
                continue
            try:
                return fn(messages=messages, tools=tools, **kwargs)
            except Exception as exc:
                errors.append(f"{adapter.provider}: {exc}")
                continue
        raise RuntimeError("All providers failed for chat_with_tools: " + "; ".join(errors))

    def chat_stream_with_tools(self, messages: list, tools: list, **kwargs: Any):
        """流式调用模型并支持工具调用"""
        errors = []
        for adapter in self.adapters:
            if not adapter.capabilities.tools:
                continue
            stream_fn = getattr(adapter, "chat_stream_with_tools", None)
            if stream_fn is None:
                # 回退到非流式
                fn = getattr(adapter, "chat_with_tools", None)
                if fn is None:
                    continue
                try:
                    result = fn(messages=messages, tools=tools, **kwargs)
                    if result.get("content"):
                        yield {"type": "content", "text": result["content"]}
                    if result.get("tool_calls"):
                        yield {"type": "tool_calls", "data": result["tool_calls"]}
                    return
                except Exception as exc:
                    errors.append(f"{adapter.provider}: {exc}")
                    continue
            try:
                for chunk in stream_fn(messages=messages, tools=tools, **kwargs):
                    yield chunk
                return
            except Exception as exc:
                errors.append(f"{adapter.provider}: {exc}")
                continue
        raise RuntimeError("All providers failed for chat_stream_with_tools: " + "; ".join(errors))
