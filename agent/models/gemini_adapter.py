from __future__ import annotations

from typing import Any

from .base import ModelAdapter, ModelCapabilities


class GeminiAdapter(ModelAdapter):
    capabilities = ModelCapabilities(chat=True, embeddings=True)

    def _client(self):
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError(
                "Google Generative AI SDK not installed. Install `google-generativeai`."
            ) from exc
        if not self.api_key:
            raise RuntimeError("Gemini API key missing. Set api_key_env or api_key.")
        genai.configure(api_key=self.api_key)
        return genai

    def chat(self, prompt: str, **kwargs: Any) -> str:
        genai = self._client()
        model_name = kwargs.get("model", self.model)
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(prompt)
        return response.text

    def embed(self, text: str, **kwargs: Any) -> list[float]:
        genai = self._client()
        model_name = kwargs.get("model", self.model)
        response = genai.embed_content(model=model_name, content=text)
        return response["embedding"]
