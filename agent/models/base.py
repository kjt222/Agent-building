from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..credentials import resolve_api_key


@dataclass(frozen=True)
class ModelCapabilities:
    chat: bool = True
    embeddings: bool = False
    tools: bool = False
    json_mode: bool = False
    multimodal: bool = False


class ModelAdapter(ABC):
    capabilities = ModelCapabilities()

    def __init__(
        self,
        provider: str,
        model: str,
        api_key_env: Optional[str] = None,
        api_key_ref: Optional[str] = None,
        api_key: Optional[str] = None,
        prefer_env: bool = True,
        **kwargs: Any,
    ) -> None:
        self.provider = provider
        self.model = model
        self.api_key_env = api_key_env
        self.api_key_ref = api_key_ref
        self.api_key = api_key or resolve_api_key(
            api_key_env=api_key_env,
            api_key_ref=api_key_ref,
            prefer_env=prefer_env,
        )
        self.extra: Dict[str, Any] = dict(kwargs)

    @abstractmethod
    def chat(self, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError

    @abstractmethod
    def embed(self, text: str, **kwargs: Any) -> list[float]:
        raise NotImplementedError
