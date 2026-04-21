from __future__ import annotations

from typing import Any

from .base import ModelAdapter, ModelCapabilities
from .http_utils import request_json


class DeepSeekAdapter(ModelAdapter):
    capabilities = ModelCapabilities(chat=True, embeddings=False, tools=True, json_mode=True)

    def _base_url(self) -> str:
        return str(self.extra.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")

    def _require_key(self) -> str:
        if not self.api_key:
            raise RuntimeError("DeepSeek API key missing. Set api_key_env or api_key.")
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
        raise RuntimeError("DeepSeek does not provide embeddings on this API.")
