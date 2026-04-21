from __future__ import annotations

from typing import Any, Dict, List, Optional
import concurrent.futures
import json

from ..credentials import resolve_api_key
from .http_utils import request_json


def _resolve_key(cfg: Dict[str, Any]) -> str:
    key = resolve_api_key(cfg.get("api_key_env"), cfg.get("api_key_ref"), prefer_env=True)
    if not key:
        raise RuntimeError("API key missing")
    return key


def _base_url_for(provider_type: str, cfg: Dict[str, Any]) -> str:
    if provider_type == "openai":
        return "https://api.openai.com/v1"
    if provider_type == "deepseek":
        return str(cfg.get("base_url") or "https://api.deepseek.com/v1").rstrip("/")
    if provider_type == "zhipu":
        return str(cfg.get("base_url") or "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    if provider_type == "openai_compat":
        return str(cfg.get("base_url") or "").rstrip("/")
    return ""


def _extract_error_code(message: str) -> Optional[str]:
    if "HTTP" not in message:
        return None
    idx = message.find(":")
    if idx == -1:
        return None
    payload = message[idx + 1 :].strip()
    try:
        data = json.loads(payload)
    except Exception:
        return None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict) and err.get("code") is not None:
            return str(err.get("code"))
        if data.get("code") is not None:
            return str(data.get("code"))
    return None


def _probe_zhipu_models(key: str, section: str, base_url: str) -> List[str]:
    llm_models = [
        "glm-4-plus",
        "glm-4-0520",
        "glm-4-air",
        "glm-4-airx",
        "glm-4-long",
        "glm-4-flash",
        "glm-4",
        "glm-4.6",
        "glm-4.7",
    ]
    emb_models = ["embedding-2", "embedding-3"]
    candidates = emb_models if section == "embedding" else llm_models
    if not candidates:
        return []
    url = f"{base_url.rstrip('/')}/embeddings" if section == "embedding" else f"{base_url.rstrip('/')}/chat/completions"

    def _check(model: str) -> tuple[str, bool, Optional[str]]:
        try:
            if section == "embedding":
                payload = {"model": model, "input": "ping"}
            else:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }
            request_json("POST", url, key, payload=payload, timeout=20.0)
            return model, True, None
        except Exception as exc:
            code = _extract_error_code(str(exc))
            if code in ("1211", "1220"):
                return model, False, code
            return model, True, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_check, candidates))

    ok_models = [model for model, ok, _ in results if ok]
    if ok_models:
        return ok_models
    codes = {code for _, _, code in results if code}
    if "1220" in codes:
        raise RuntimeError("Zhipu model access denied for this API key.")
    if "1211" in codes:
        raise RuntimeError("Zhipu model not found.")
    raise RuntimeError("Zhipu model probe failed.")


def _parse_model_list(data: Any) -> List[str]:
    candidates: list[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("data"), list):
            candidates = data["data"]
        elif isinstance(data.get("models"), list):
            candidates = data["models"]
        elif isinstance(data.get("model_list"), list):
            candidates = data["model_list"]
    elif isinstance(data, list):
        candidates = data
    models: list[str] = []
    for item in candidates:
        if isinstance(item, str):
            models.append(item)
            continue
        if isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("model")
            if model_id:
                models.append(str(model_id))
    return sorted({name for name in models if name})


def list_provider_models(provider_type: str, cfg: Dict[str, Any], section: Optional[str] = None) -> List[str]:
    key = _resolve_key(cfg)
    if provider_type in ("openai", "openai_compat", "deepseek"):
        base_url = _base_url_for(provider_type, cfg)
        if not base_url:
            raise RuntimeError("Base URL required")
        url = f"{base_url.rstrip('/')}/models"
        data = request_json("GET", url, key, payload=None)
        models = _parse_model_list(data)
        if not models:
            if provider_type == "deepseek":
                return ["deepseek-chat", "deepseek-reasoner"] if section != "embedding" else []
            raise RuntimeError("No models returned")
        if section == "embedding":
            return [m for m in models if "embedding" in m]
        if section == "llm":
            return [m for m in models if "embedding" not in m]
        return models
    if provider_type == "zhipu":
        base_url = _base_url_for(provider_type, cfg) or "https://open.bigmodel.cn/api/paas/v4"
        return _probe_zhipu_models(key, section or "llm", base_url)
    if provider_type == "gemini":
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError("Gemini SDK not installed") from exc
        genai.configure(api_key=key)
        models = genai.list_models()
        names = sorted([model.name for model in models])
        if section == "embedding":
            return [m for m in names if "embedding" in m]
        if section == "llm":
            return [m for m in names if "embedding" not in m]
        return names
    raise RuntimeError(f"Unsupported provider: {provider_type}")


def test_provider(section: str, provider_type: str, cfg: Dict[str, Any]) -> None:
    models = list_provider_models(provider_type, cfg, section=section)
    if not models:
        raise RuntimeError("No models returned")
