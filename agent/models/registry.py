from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from .base import ModelAdapter
from .fallback_adapter import FallbackAdapter
from .deepseek_adapter import DeepSeekAdapter
from .gemini_adapter import GeminiAdapter
from .openai_adapter import OpenAIAdapter
from .zhipu_adapter import ZhipuAdapter


class ModelRegistry:
    def __init__(self) -> None:
        self._providers: Dict[str, Type[ModelAdapter]] = {}

    def register(self, name: str, adapter_cls: Type[ModelAdapter]) -> None:
        self._providers[name] = adapter_cls

    def create(self, provider_type: str, config: Dict[str, Any], provider_id: Optional[str] = None) -> ModelAdapter:
        if provider_type not in self._providers:
            raise KeyError(f"Unknown provider: {provider_type}")
        adapter_cls = self._providers[provider_type]
        payload = dict(config)
        payload.pop("type", None)
        payload.setdefault("provider_type", provider_type)
        return adapter_cls(provider=provider_id or provider_type, **payload)

    def _select_section(
        self, models_config: Dict[str, Any], section: str, profile: Optional[str]
    ) -> Dict[str, Any]:
        if "profiles" in models_config:
            if not profile:
                raise KeyError("Missing profile for models config")
            profile_cfg = models_config.get("profiles", {}).get(profile)
            if not profile_cfg:
                raise KeyError(f"Missing profile config: {profile}")
            if section not in profile_cfg:
                raise KeyError(f"Missing section: {section}")
            return profile_cfg[section]
        if section not in models_config:
            raise KeyError(f"Missing section: {section}")
        return models_config[section]

    def create_from_config(
        self, models_config: Dict[str, Any], section: str, profile: Optional[str] = None
    ) -> ModelAdapter:
        section_cfg = self._select_section(models_config, section, profile)
        active = section_cfg.get("active")
        providers = section_cfg.get("providers", {})
        fallback = section_cfg.get("fallback", [])
        if not active:
            raise ValueError(f"Active provider not set for section: {section}")
        if active not in providers:
            raise KeyError(f"Provider config not found: {active}")
        order: List[str] = [active] + [p for p in fallback if p != active]
        adapters: List[ModelAdapter] = []
        for provider_id in order:
            if provider_id not in providers:
                continue
            provider_cfg = providers[provider_id]
            provider_type = provider_cfg.get("type", provider_id)
            adapters.append(self.create(provider_type, provider_cfg, provider_id=provider_id))
        if not adapters:
            raise KeyError(f"No valid providers for section: {section}")
        if len(adapters) == 1:
            return adapters[0]
        return FallbackAdapter(adapters)


def default_registry() -> ModelRegistry:
    registry = ModelRegistry()
    registry.register("openai", OpenAIAdapter)
    registry.register("openai_compat", OpenAIAdapter)
    registry.register("deepseek", DeepSeekAdapter)
    registry.register("gemini", GeminiAdapter)
    registry.register("zhipu", ZhipuAdapter)
    return registry
