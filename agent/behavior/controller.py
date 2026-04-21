from __future__ import annotations

from typing import Any, Dict, Optional

from ..config_loader import load_behavior_config


def _merge(base: Dict[str, Any], override: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not override:
        return base
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def _normalize_system_prompt(prompt: Optional[str], language: Optional[str]) -> Optional[str]:
    if not prompt and not language:
        return None
    content = prompt or ""
    if language:
        lang_line = "请使用中文回复。" if language.lower().startswith("zh") else "Please reply in English."
        if lang_line not in content:
            content = f"{content}\n{lang_line}".strip()
    return content.strip() if content else None


def resolve_behavior(
    config_dir: str | None,
    profile: str,
    provider: str,
    model: str,
) -> Dict[str, Any]:
    cfg = load_behavior_config(config_dir)
    result: Dict[str, Any] = {}

    # Global defaults
    result = _merge(result, cfg.get("defaults"))

    # Provider defaults
    providers = cfg.get("providers", {})
    provider_cfg = providers.get(provider, {})
    result = _merge(result, provider_cfg.get("defaults") or {})

    # Provider model overrides
    model_overrides = (provider_cfg.get("models") or {}).get(model)
    result = _merge(result, model_overrides)

    # Profile defaults
    profile_cfg = (cfg.get("profiles") or {}).get(profile, {})
    result = _merge(result, profile_cfg.get("defaults") or {})

    # Profile provider defaults
    prof_providers = profile_cfg.get("providers", {})
    prof_provider_cfg = prof_providers.get(provider, {})
    result = _merge(result, prof_provider_cfg.get("defaults") or {})

    # Profile provider model overrides
    prof_model_cfg = (prof_provider_cfg.get("models") or {}).get(model)
    result = _merge(result, prof_model_cfg)

    # Normalize system prompt with language lock
    system_prompt = _normalize_system_prompt(result.get("system_prompt"), result.get("language"))
    if system_prompt:
        result["system_prompt"] = system_prompt
    return result


def build_llm_kwargs(behavior: Dict[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    for key in ("system_prompt", "temperature", "top_p", "max_tokens", "stop", "response_format"):
        value = behavior.get(key)
        if value is not None and value != "":
            kwargs[key] = value
    extra_body = behavior.get("extra_body")
    if isinstance(extra_body, dict) and extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs
