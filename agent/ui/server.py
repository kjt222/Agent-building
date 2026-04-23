from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from time import perf_counter
import concurrent.futures
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from urllib.parse import urlparse, urlunparse
from urllib.request import Request as APIRequest, urlopen
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config_loader import (
    get_config_dir,
    load_app_config,
    load_models_config,
    load_rag_config,
    save_yaml,
)
from ..credentials import describe_key, store_api_key, resolve_api_key
from ..init_setup import DEFAULT_PROFILE_YAML, DEFAULT_TXT
from ..logging_utils import log_event
from ..models import default_registry
from ..models.ops import list_provider_models, test_provider
from ..models.http_utils import request_json
from ..behavior import resolve_behavior, build_llm_kwargs
from ..privacy import load_lexicons, mask_text
from ..profile import resolve_profile
from ..rag import NO_CONTEXT_MESSAGE, RagService, SqliteVectorStore, answer_question, build_context, build_prompt
from ..rag.service import RagConfig
from ..rag.store import SearchResult, SqliteVecStore
from ..storage.conversation_adapter import ConversationManagerV2 as ConversationManager
from ..storage.knowledge_manager import KnowledgeManager
from ..core import AgentExecutor, AgentConfig, MemoryManager, get_memory_manager, create_compactor, CompactionConfig
from ..tools.registry import get_registry
from ..tools.knowledge import create_kb_tools
from ..tools.filesystem import create_filesystem_tools
from ..tools.system import create_system_tools


PROVIDERS = ("openai", "anthropic", "deepseek", "gemini", "zhipu", "openai_compat")
VENDOR_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com",
    "gemini": "",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
}


def _create_agent_loop_adapter(
    provider_type: str,
    model_name: str,
    api_key: str,
    base_url: Optional[str] = None,
):
    """Create a provider-neutral AgentLoop adapter from profile settings."""
    from ..models.openai_adapter_v2 import OpenAIAdapter
    from ..models.openai_responses_adapter import OpenAIResponsesAdapter
    from ..models.agent_loop_adapters import (
        AnthropicAgentLoopAdapter,
        GeminiAgentLoopAdapter,
    )

    provider = (provider_type or "openai").lower()
    if provider == "openai" and model_name.startswith("gpt-5"):
        return OpenAIResponsesAdapter(model=model_name, api_key=api_key, base_url=base_url)
    if provider in {"openai", "openai_compat"}:
        return OpenAIAdapter(model=model_name, api_key=api_key, base_url=base_url)
    if provider == "deepseek":
        return OpenAIAdapter(
            model=model_name,
            api_key=api_key,
            base_url=base_url or "https://api.deepseek.com/v1",
        )
    if provider == "anthropic":
        return AnthropicAgentLoopAdapter(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
        )
    if provider == "gemini":
        return GeminiAgentLoopAdapter(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
        )
    raise RuntimeError(f"Unsupported AgentLoop v2 provider: {provider_type}")


def _config_paths(config_dir: str | None) -> Dict[str, Path]:
    base = get_config_dir(config_dir)
    return {
        "base": base,
        "models": base / "models.yaml",
        "app": base / "app.yaml",
    }


def _profile_models(models: dict, profile: str) -> dict:
    profiles = models.get("profiles", {})
    if profile not in profiles:
        profiles[profile] = {}
    return profiles[profile]


def _ensure_section(profile_cfg: dict, section: str) -> dict:
    return profile_cfg.setdefault(section, {})


def _ensure_provider(cfg: dict, name: str) -> dict:
    providers = cfg.setdefault("providers", {})
    if name not in providers:
        providers[name] = {}
    return providers[name]


def _key_ref(profile: str, section: str, provider: str) -> str:
    return f"{profile}.{section}.{provider}"


def _profile_active_llm_model(models_cfg: dict, name: str) -> str:
    cfg = (models_cfg.get("profiles") or {}).get(name) or {}
    llm = cfg.get("llm") or {}
    active = llm.get("active") or ""
    providers = llm.get("providers") or {}
    model = (providers.get(active) or {}).get("model") or ""
    if active and model:
        return f"{active} / {model}"
    return active or "—"


def _profile_active_image_gen_model(models_cfg: dict, name: str) -> Optional[str]:
    cfg = (models_cfg.get("profiles") or {}).get(name) or {}
    img = cfg.get("image_gen") or {}
    active = img.get("active")
    if not active:
        return None
    providers = img.get("providers") or {}
    model = (providers.get(active) or {}).get("model")
    return model or None


def _profile_active_llm_provider(models_cfg: dict, name: str) -> tuple[str, dict]:
    cfg = (models_cfg.get("profiles") or {}).get(name) or {}
    llm = cfg.get("llm") or {}
    active = llm.get("active") or llm.get("vendor") or ""
    providers = llm.get("providers") or {}
    provider_cfg = dict(providers.get(active) or {})
    return active, provider_cfg


def _key_statuses(providers: dict) -> dict:
    return {
        name: describe_key(
            providers.get(name, {}).get("api_key_env"),
            providers.get(name, {}).get("api_key_ref"),
        )
        for name in PROVIDERS
    }


def _detect_provider(key: str, base_url: Optional[str]) -> Optional[str]:
    if base_url:
        host = urlparse(base_url).netloc.lower()
        if "deepseek" in host:
            return "deepseek"
        if "bigmodel" in host or "zhipu" in host:
            return "zhipu"
        return "openai_compat"
    if key.startswith("AIza"):
        return "gemini"
    if key.startswith("sk-ant"):
        return "anthropic"
    if key.startswith("sk-") or key.startswith("rk-"):
        return "openai"
    return None


def _default_models(provider: str, section: str) -> list[str]:
    if provider == "openai":
        if section == "embedding":
            return ["text-embedding-3-small", "text-embedding-3-large"]
        return [
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o-mini",
            "gpt-4o",
            "gpt-4",
            "gpt-3.5-turbo",
        ]
    if provider == "anthropic":
        if section == "embedding":
            return []
        return [
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-1-20250805",
            "claude-3-7-sonnet-20250219",
            "claude-3-5-sonnet-20241022",
        ]
    if provider == "gemini":
        if section == "embedding":
            return ["text-embedding-004"]
        return ["models/gemini-1.5-flash", "models/gemini-1.5-pro", "models/gemini-1.0-pro"]
    if provider == "deepseek":
        if section == "embedding":
            return []
        return ["deepseek-chat", "deepseek-reasoner"]
    if provider == "zhipu":
        if section == "embedding":
            return ["embedding-2", "embedding-3"]
        return [
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
    return []


def _parse_model_list(data: Any) -> list[str]:
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


def _fetch_models_http(base_url: str, key: str) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    data = request_json("GET", url, key, payload=None)
    return _parse_model_list(data)


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


def _compact_json(value: Any, limit: int = 500) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ": "))
    cwd = str(Path.cwd())
    text = text.replace(cwd, ".")
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def _summarize_tool_input(tool_name: str, tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return _compact_json(tool_input)
    path = tool_input.get("path") or tool_input.get("directory")
    if path:
        try:
            path = str(Path(path).resolve().relative_to(Path.cwd()))
        except Exception:
            path = str(path).replace(str(Path.cwd()), ".")
    if tool_name == "Bash":
        return _compact_json({
            "command": tool_input.get("command", ""),
            "timeout": tool_input.get("timeout", 60),
        })
    if tool_name in {
        "Read",
        "Write",
        "Edit",
        "Grep",
        "Glob",
        "KnowledgeSearch",
        "KnowledgeIndex",
        "ExcelRead",
        "ExcelEdit",
        "WordRead",
        "WordEdit",
        "RenderDocument",
    }:
        summarized = dict(tool_input)
        if path:
            if "path" in summarized:
                summarized["path"] = path
            if "directory" in summarized:
                summarized["directory"] = path
        if "content" in summarized and isinstance(summarized["content"], str):
            summarized["content"] = f"<{len(summarized['content'])} chars>"
        return _compact_json(summarized)
    return _compact_json(tool_input)


def _summarize_tool_result(content: Any, *, limit: int = 600) -> str:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    text = text.replace(str(Path.cwd()), ".")
    text = text.replace("\r\n", "\n")
    lines = text.splitlines()
    if len(lines) > 18:
        text = "\n".join(lines[:18]) + "\n...<truncated>"
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


_ARTIFACT_INTENT_RE = re.compile(
    r"(?:write|create|edit|modify|fix|implement|build|run|test|render|"
    r"code|html|css|javascript|python|file|repo|frontend|snake|game|"
    r"写|创建|修改|修复|实现|运行|测试|渲染|代码|文件|前端|复刻|贪吃蛇|游戏)",
    re.IGNORECASE,
)
_KNOWLEDGE_INTENT_RE = re.compile(
    r"(?:knowledge|kb|search|pdf|document|docx|xlsx|memory|fact|"
    r"知识库|搜索|检索|资料|文档|记忆|事实|PDF)",
    re.IGNORECASE,
)
_OFFICE_EXCEL_INTENT_RE = re.compile(
    r"(?:excel|xlsx|xlsm|spreadsheet|workbook|worksheet|sheet|office|"
    r"\u8868\u683c|\u5de5\u4f5c\u7c3f|\u5de5\u4f5c\u8868|"
    r"\u7535\u5b50\u8868\u683c|\u4fee\u6539\u8868\u683c|"
    r"\u8868\u683c\u683c\u5f0f)",
    re.IGNORECASE,
)
_OFFICE_WORD_INTENT_RE = re.compile(
    r"(?:word|docx|\\.docx|word document|document formatting|"
    r"\u6587\u6863\u683c\u5f0f|\u4fee\u6539\u6587\u6863|"
    r"\u6bb5\u843d|\u6807\u9898|\u56fe\u6ce8|\u6b63\u6587)",
    re.IGNORECASE,
)


def _select_v2_tools_for_turn(message: str, images: list, app_cfg: dict) -> tuple[dict, str]:
    """Progressively disclose only the tool surface useful for this turn."""
    from ..tools_v2.excel_tool import ExcelEditTool, ExcelReadTool
    from ..tools_v2.knowledge_tool import KnowledgeIndexTool, KnowledgeSearchTool
    from ..tools_v2.primitives import default_toolset, full_toolset
    from ..tools_v2.render_tool import RenderDocumentTool
    from ..tools_v2.word_tool import WordEditTool, WordReadTool

    text = message or ""
    selected: dict = {}
    if _OFFICE_WORD_INTENT_RE.search(text):
        base_tools = default_toolset()
        for name in ("Read", "Glob"):
            selected[name] = base_tools[name]
        selected["WordRead"] = WordReadTool()
        selected["WordEdit"] = WordEditTool()
        selected["RenderDocument"] = RenderDocumentTool()
        return selected, "office_word"

    if _OFFICE_EXCEL_INTENT_RE.search(text):
        base_tools = default_toolset()
        for name in ("Read", "Glob"):
            selected[name] = base_tools[name]
        selected["ExcelRead"] = ExcelReadTool()
        selected["ExcelEdit"] = ExcelEditTool()
        selected["RenderDocument"] = RenderDocumentTool()
        return selected, "office_excel"

    if _ARTIFACT_INTENT_RE.search(text):
        selected.update(full_toolset())
        selected["KnowledgeSearch"] = KnowledgeSearchTool()
        selected["KnowledgeIndex"] = KnowledgeIndexTool()
        return selected, "artifact"

    if images and _ARTIFACT_INTENT_RE.search(text + " image screenshot screenshot"):
        selected.update(full_toolset())
        return selected, "visual_artifact"

    if _KNOWLEDGE_INTENT_RE.search(text):
        for name, tool in full_toolset().items():
            if name in {"Read", "Glob", "Grep"}:
                selected[name] = tool
        selected["KnowledgeSearch"] = KnowledgeSearchTool()
        return selected, "knowledge"

    return selected, "direct"


def _probe_zhipu_models(key: str, section: str, base_url: Optional[str]) -> list[str]:
    candidates = _default_models("zhipu", section)
    if not candidates:
        return []
    base = (base_url or VENDOR_BASE_URLS["zhipu"]).rstrip("/")
    url = f"{base}/chat/completions" if section != "embedding" else f"{base}/embeddings"

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
            # Unknown errors/timeouts -> keep to avoid false negatives.
            return model, True, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(_check, candidates))

    ok_models = [model for model, ok, _ in results if ok]
    if ok_models:
        return ok_models
    # If all failed with permission/not-found, surface an error.
    codes = {code for _, _, code in results if code}
    if "1220" in codes:
        raise RuntimeError("Zhipu model access denied for this API key.")
    if "1211" in codes:
        raise RuntimeError("Zhipu model not found.")
    raise RuntimeError("Zhipu model probe failed.")


def _filter_by_section(models: list[str], section: str | None) -> list[str]:
    if not section:
        return models
    if section == "embedding":
        return [name for name in models if "embedding" in name]
    if section == "llm":
        return [name for name in models if "embedding" not in name]
    return models


def _list_models_with_key(
    provider_type: str,
    key: str,
    base_url: Optional[str],
    section: str | None = None,
) -> list[str]:
    if provider_type in ("openai", "deepseek", "openai_compat"):
        if provider_type == "openai":
            base_url = "https://api.openai.com/v1"
        elif provider_type == "deepseek":
            base_url = base_url or "https://api.deepseek.com/v1"
        if not base_url:
            raise RuntimeError("Base URL required.")
        models = _fetch_models_http(base_url, key)
        if not models and provider_type == "deepseek":
            models = _default_models("deepseek", section)
        filtered = _filter_by_section(models, section)
        return filtered or models
    if provider_type == "zhipu":
        return _probe_zhipu_models(key, section or "llm", base_url)
    if provider_type == "gemini":
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError("Gemini SDK not installed") from exc
        def _call():
            genai.configure(api_key=key)
            return genai.list_models()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_call)
            try:
                models = future.result(timeout=15.0)
            except concurrent.futures.TimeoutError as exc:
                raise RuntimeError("Model list request timed out.") from exc
        names = sorted([model.name for model in models])
        return _filter_by_section(names, section)
    raise RuntimeError(f"Unsupported provider: {provider_type}")


def _detect_provider_by_index(
    key: str, base_url: Optional[str], section: str
) -> tuple[str, list[str], Optional[str]]:
    if base_url:
        provider_guess = _detect_provider("", base_url) or "openai_compat"
        try:
            models = _list_models_with_key(provider_guess, key, base_url, section)
            return provider_guess, models, None
        except Exception:
            candidates = _candidate_models_for_base(base_url, section)
            probed = _probe_chat_models(base_url, key, candidates) if section == "llm" else []
            if probed:
                return provider_guess, probed, "Model list unavailable; detected via probe."
            return provider_guess, [], "Model list unavailable. Enter model ID manually."
    try:
        models = _list_models_with_key("openai", key, None, section)
        return "openai", models, None
    except Exception as exc:
        if key.startswith("sk-") or key.startswith("rk-"):
            warning = f"Model list unavailable ({exc}). Using default list."
            return "openai", _default_models("openai", section), warning
    try:
        models = _list_models_with_key("gemini", key, None, section)
        return "gemini", models, None
    except Exception as exc:
        if key.startswith("AIza"):
            warning = f"Model list unavailable ({exc}). Using default list."
            return "gemini", _default_models("gemini", section), warning
        raise RuntimeError("Unable to detect provider from API index.") from exc


def _sanitize_profile_name(raw: str) -> str:
    name = raw.strip()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9_.\-]", "", name)
    return name


def _normalize_kb_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path


def _normalize_base_url(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    path = parsed.path or ""
    if not netloc and parsed.path:
        netloc = parsed.path
        path = ""
    path = path.rstrip("/")
    segments = [segment for segment in path.split("/") if segment]
    if "v1" in segments:
        v1_index = segments.index("v1")
        path = "/" + "/".join(segments[: v1_index + 1])
    elif not path:
        path = "/v1"
    return urlunparse((scheme, netloc, path, "", "", ""))


def _candidate_models_for_base(base_url: str, section: str) -> list[str]:
    host = urlparse(base_url).netloc.lower()
    if "deepseek" in host and section == "llm":
        return ["deepseek-chat", "deepseek-reasoner"]
    if "bigmodel" in host and section == "llm":
        return [
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
    return []


def _probe_chat_models(base_url: str, key: str, candidates: list[str]) -> list[str]:
    if not candidates:
        return []
    working: list[str] = []
    for model in candidates:
        try:
            request_json(
                "POST",
                base_url.rstrip("/") + "/chat/completions",
                key,
                payload={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                },
            )
            working.append(model)
        except Exception:
            continue
    return working


def _list_openai_compat_models(base_url: str, key: str, section: str | None = None) -> list[str]:
    # Zhipu AI does not support /models; return static list filtered by section.
    if "bigmodel.cn" in base_url:
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
        if section == "embedding":
            return emb_models
        if section == "llm":
            return llm_models
        return llm_models + emb_models

    url = base_url.rstrip("/") + "/models"
    request = APIRequest(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urlopen(request, timeout=12) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError):
        # Fallback for providers that don't implement /models
        return []
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Model list response was not JSON.") from exc

    candidates: list[str] = []
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
    unique = sorted({name for name in models if name})
    if not unique:
        raise RuntimeError("Model list response missing model IDs.")
    return unique


def _kb_db_path(name: str, base_dir: Path) -> Path:
    return base_dir / "data" / "kb" / name / "rag.sqlite"


def _find_kb_entry(app_cfg: dict, name: str) -> Optional[dict]:
    for entry in app_cfg.get("knowledge_bases", []):
        if entry.get("name") == name:
            return entry
    return None


def _active_kb_list(app_cfg: dict) -> list[str]:
    active = app_cfg.get("active_kbs")
    if isinstance(active, list):
        items = [str(name).strip() for name in active if str(name).strip()]
        unique: list[str] = []
        for name in items:
            if name not in unique:
                unique.append(name)
        return unique
    legacy = str(app_cfg.get("active_kb") or "").strip()
    return [legacy] if legacy else []


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _legacy_tool_info(tool: Any) -> dict:
    return {
        "name": getattr(tool, "name", ""),
        "category": _enum_value(getattr(tool, "category", "")),
        "permission": _enum_value(getattr(tool, "permission", "")),
        "parallel_safe": None,
    }


def _v2_tool_info(tool: Any) -> dict:
    return {
        "name": getattr(tool, "name", ""),
        "category": "v2",
        "permission": _enum_value(getattr(tool, "permission_level", "")),
        "parallel_safe": bool(getattr(tool, "parallel_safe", False)),
    }


def _skip_kb_reason(question: str) -> str:
    text = question.strip().lower()
    if not text:
        return "empty"
    if len(text) <= 4:
        return "short"
    if re.fullmatch(r"[\W_]+", text):
        return "punct"

    kb_hints = (
        "资料库", "文件", "文档", "项目", "引用", "出处", "来源",
        "paper", "pdf", "ppt", "课件", "论文", "报告", "手册",
        "根据", "参考", "查阅", "查找", "搜索", "检索",
        "总结一下", "概括", "归纳",
    )
    if any(hint in text for hint in kb_hints):
        return "kb_hint"

    generic_patterns = (
        r"^你好", r"^您好", r"^在吗", r"^嗨", r"^hi\b", r"^hey\b", r"^hello",
        r"谢谢", r"感谢", r"thanks", r"thank you",
        r"你是谁", r"你是什么", r"你能做什么", r"你会什么", r"介绍一下你",
        r"what are you", r"who are you",
        r"^好的?$", r"^ok", r"^是的?$", r"^不是?$", r"^对$", r"^嗯",
        r"^明白", r"^懂了", r"^知道了", r"^收到",
    )
    if any(re.search(pattern, text) for pattern in generic_patterns):
        return "generic"

    chat_patterns = (
        r"天气", r"几点", r"时间", r"日期", r"星期",
        r"weather", r"time", r"date",
        r"怎么样$",
        r"吗[？?]?$",
    )
    if len(text) <= 30 and any(re.search(pattern, text) for pattern in chat_patterns):
        return "chat_pattern"

    creative_patterns = (
        r"写.{0,4}(诗|故事|文章|歌|笑话|段子)",
        r"讲.{0,4}(故事|笑话|段子)",
        r"编.{0,4}(故事|段子)",
        r"write.{0,10}(poem|story|song|joke)",
        r"tell.{0,10}(story|joke)",
    )
    if any(re.search(pattern, text) for pattern in creative_patterns):
        return "creative"

    if re.fullmatch(r"[0-9\s\+\-\*/×÷=().<>%^\?]+", text):
        return "math"
    if re.search(r"\d", text):
        if len(text) <= 20 and re.search(r"(等于|等於|多少|几|幾|加|减|減|乘|除|算|plus|minus|times|divide|add|subtract|multiply|divide)", text):
            return "math"

    if len(text) <= 10 and not re.search(r"[A-Za-z0-9]", text):
        return "short_cn"

    return ""


def _should_skip_kb(question: str) -> bool:
    reason = _skip_kb_reason(question)
    if reason and reason != "kb_hint":
        return True
    return False


def _filter_sources_by_citations(answer: str, sources: list[dict]) -> list[dict]:
    if not sources or not answer:
        return []
    cited = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}
    if not cited:
        return []
    return [src for idx, src in enumerate(sources, start=1) if idx in cited]


def _set_active_kbs(app_cfg: dict, active_kbs: list[str]) -> list[str]:
    unique: list[str] = []
    for name in active_kbs:
        name = str(name).strip()
        if name and name not in unique:
            unique.append(name)
    app_cfg["active_kbs"] = unique
    app_cfg["active_kb"] = unique[0] if unique else ""
    return unique


def _index_kb_paths(
    config_dir: str | None,
    base_dir: Path,
    app_cfg: dict,
    kb_name: str,
    profile_name: str | None,
    target_paths: Optional[list[Path]] = None,
) -> int:
    entry = _find_kb_entry(app_cfg, kb_name)
    if not entry:
        return 0
    kb_path = Path(entry.get("path", ""))
    if not kb_path.exists():
        return 0
    db_path_value = entry.get("db_path")
    db_path = Path(db_path_value) if db_path_value else _kb_db_path(kb_name, base_dir)
    try:
        rag_service = _build_rag_service(config_dir, profile_name, db_path)
    except Exception:
        return 0
    indexed = 0
    if target_paths:
        for path in target_paths:
            try:
                indexed += rag_service.index_path(Path(path), force=False)
            except Exception:
                continue
    else:
        try:
            indexed = rag_service.index_path(kb_path, force=False)
        except Exception:
            indexed = 0

    # Incremental embedding: populate sqlite-vec for newly indexed files
    if indexed > 0:
        _embed_indexed_files(rag_service.embedder, db_path, kb_name)

    return indexed


def _embed_indexed_files(embedder, db_path: Path, kb_name: str) -> None:
    """Embed files that are in file_index but not yet in vec_chunks."""
    vec_store = _try_build_vec_store(embedder, db_path)
    if vec_store is None:
        return
    try:
        from ..rag.chunker import split_text
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, filename, content FROM file_index WHERE kb_name = ?",
            (kb_name,),
        ).fetchall()
        for row in rows:
            file_id = row["id"]
            if vec_store.has_chunks_for_file(file_id):
                continue  # Already embedded
            content = row["content"]
            if not content:
                continue
            chunks = split_text(content, 500, 50)
            if not chunks:
                continue
            embeddings = []
            for chunk in chunks:
                try:
                    embeddings.append(embedder.embed(chunk))
                except Exception:
                    break
            else:
                # All chunks embedded successfully
                vec_store.delete_by_file(file_id)
                vec_store.add_chunks(file_id, chunks, embeddings)
        conn.close()
    except Exception:
        pass
    finally:
        vec_store.close()


def _open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
        return
    subprocess.Popen(["xdg-open", str(path)])


def _safe_filename(name: str) -> str:
    return Path(name).name


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for idx in range(1, 1000):
        candidate = parent / f"{stem}_{idx}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("Unable to allocate unique filename.")


def _build_rag_config(
    config_dir: str | None, profile_name: str | None, db_path: Path
) -> RagConfig:
    rag_config = load_rag_config(config_dir)
    index_cfg = rag_config.get("index", {})
    return RagConfig(
        db_path=db_path,
        chunk_size=int(index_cfg.get("chunk_size", 800)),
        chunk_overlap=int(index_cfg.get("chunk_overlap", 150)),
        top_k=int(index_cfg.get("top_k", 6)),
        score_threshold=float(index_cfg.get("score_threshold", 0.2)),
        max_context_chars=int(index_cfg.get("max_context_chars", 8000)),
        extensions=tuple(index_cfg.get("extensions", [".txt", ".pdf", ".docx", ".xlsx"])),
    )


def _build_rag_service(config_dir: str | None, profile_name: str | None, db_path: Path) -> RagService:
    models_config = load_models_config(config_dir)
    registry = default_registry()
    embedder = registry.create_from_config(models_config, "embedding", profile=profile_name)
    config = _build_rag_config(config_dir, profile_name, db_path)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteVectorStore(config.db_path)
    return RagService(embedder=embedder, store=store, config=config)


# Embedding dimension mapping for known models.
# Fallback: probe the embedder with a test string.
_KNOWN_EMBEDDING_DIMS: dict[str, int] = {
    "text-embedding-3-large": 3072,
    "text-embedding-3-small": 1536,
    "text-embedding-ada-002": 1536,
    "text-embedding-004": 768,
    "embedding-3": 2048,
    "embedding-2": 1024,
}


def _detect_embedding_dim(embedder) -> int:
    """Detect embedding dimension from model name or by probing."""
    model_name = getattr(embedder, "model", "")
    if model_name in _KNOWN_EMBEDDING_DIMS:
        return _KNOWN_EMBEDDING_DIMS[model_name]
    # Probe: embed a short text to detect dimension
    try:
        vec = embedder.embed("dimension probe")
        return len(vec)
    except Exception:
        return 1024  # safe default


def _try_build_vec_store(embedder, db_path: Path) -> SqliteVecStore | None:
    """Try to create a SqliteVecStore. Returns None if sqlite-vec unavailable."""
    if not SqliteVecStore.is_available():
        return None
    try:
        model_name = getattr(embedder, "model", "unknown")
        dim = _detect_embedding_dim(embedder)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteVecStore(db_path, model_name, dim)
    except Exception:
        return None


def _kb_hybrid_search(
    config_dir: str | None,
    profile_name: str | None,
    app_cfg: dict,
    active_kbs: list[str],
    question: str,
    base_dir: Path,
    top_k: int = 6,
) -> tuple[list[SearchResult], list[dict]]:
    """Hybrid KB search across active knowledge bases.

    Uses SqliteVecStore + KnowledgeManager.hybrid_search when available,
    falls back to old rag_service.query() otherwise.

    Returns:
        (results, metrics) — results as list[SearchResult], metrics as list[dict]
    """
    all_results: list[SearchResult] = []
    kb_metrics: list[dict] = []

    for kb_name in active_kbs:
        entry = _find_kb_entry(app_cfg, kb_name)
        if not entry:
            continue
        db_path_value = entry.get("db_path")
        if db_path_value:
            db_path = Path(db_path_value)
        else:
            db_path = _kb_db_path(kb_name, base_dir)

        try:
            rag_service = _build_rag_service(config_dir, profile_name, db_path)
        except Exception:
            continue

        embedder = rag_service.embedder
        vec_store = _try_build_vec_store(embedder, db_path)

        if vec_store is not None:
            # Hybrid path: FTS5 + sqlite-vec via KnowledgeManager
            from ..storage.database import Database
            km_db = Database(db_path)
            km = KnowledgeManager(
                db=km_db,
                vec_store=vec_store,
                embedder=embedder,
            )
            try:
                merged = km.hybrid_search(question, [kb_name], top_k=top_k)
                for item in merged:
                    all_results.append(SearchResult(
                        doc_id=f"hybrid:{item['file_id']}",
                        text=item.get("chunk_text") or item.get("snippet", ""),
                        metadata={
                            "source_path": item.get("filename", ""),
                            "kb_name": kb_name,
                            "source": item.get("source", "hybrid"),
                        },
                        score=item.get("rrf_score", 0.0),
                    ))
                kb_metrics.append({"kb": kb_name, "strategy": "hybrid"})
                continue  # success, skip fallback
            except Exception:
                pass  # fall through to legacy
            finally:
                vec_store.close()

        # Legacy fallback: old rag_service.query()
        try:
            results = rag_service.query(question)
            metrics = dict(rag_service.last_metrics or {})
            metrics["kb"] = kb_name
            metrics["strategy"] = "legacy"
            kb_metrics.append(metrics)
            for item in results:
                metadata = dict(item.metadata)
                metadata["kb_name"] = kb_name
                all_results.append(SearchResult(
                    doc_id=item.doc_id,
                    text=item.text,
                    metadata=metadata,
                    score=item.score,
                ))
        except Exception:
            continue

    # Sort by score descending
    all_results.sort(key=lambda item: item.score, reverse=True)
    return all_results[:top_k], kb_metrics


def _default_profile_entry(name: str) -> dict:
    return {
        "rag_db_path": f"data/{name}/rag.sqlite",
        "logs_dir": f"logs/{name}",
        "lexicon_files": [
            "lexicons/global.yaml",
            f"lexicons/{name}.yaml",
            "lexicons/global.txt",
            f"lexicons/{name}.txt",
        ],
        "cloud_send": "raw",
        "allow_raw_on_confirm": True,
        "conflict_confirm": False,
        "vector_store_content": "raw",
    }


def _ensure_profile_assets(base_dir: Path, name: str) -> None:
    (base_dir / "data" / name).mkdir(parents=True, exist_ok=True)
    (base_dir / "logs" / name).mkdir(parents=True, exist_ok=True)
    (base_dir / "lexicons").mkdir(parents=True, exist_ok=True)
    yaml_path = base_dir / "lexicons" / f"{name}.yaml"
    if not yaml_path.exists():
        save_yaml(yaml_path, DEFAULT_PROFILE_YAML)
    txt_path = base_dir / "lexicons" / f"{name}.txt"
    if not txt_path.exists():
        txt_path.write_text(DEFAULT_TXT, encoding="utf-8")


def _default_models_profile(name: str) -> dict:
    return {
        "llm": {
            "active": "",
            "fallback": [],
            "providers": {
                "openai": {
                    "type": "openai",
                    "model": "",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key_ref": _key_ref(name, "llm", "openai"),
                },
                "deepseek": {
                    "type": "deepseek",
                    "model": "",
                    "base_url": "",
                    "api_key_env": "",
                    "api_key_ref": _key_ref(name, "llm", "deepseek"),
                },
                "zhipu": {
                    "type": "zhipu",
                    "model": "",
                    "base_url": "",
                    "api_key_env": "",
                    "api_key_ref": _key_ref(name, "llm", "zhipu"),
                },
            },
        },
        "embedding": {
            "active": "",
            "fallback": [],
            "providers": {
                "openai": {
                    "type": "openai",
                    "model": "",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key_ref": _key_ref(name, "embedding", "openai"),
                },
                "deepseek": {
                    "type": "deepseek",
                    "model": "",
                    "base_url": "",
                    "api_key_env": "",
                    "api_key_ref": _key_ref(name, "embedding", "deepseek"),
                },
                "zhipu": {
                    "type": "zhipu",
                    "model": "",
                    "base_url": "",
                    "api_key_env": "",
                    "api_key_ref": _key_ref(name, "embedding", "zhipu"),
                },
            },
        },
    }


def _clone_models_profile(models_cfg: dict, source: str, target: str) -> None:
    profiles = models_cfg.setdefault("profiles", {})
    template = profiles.get(source)
    if not template and profiles:
        template = next(iter(profiles.values()))
    if template:
        cloned = copy.deepcopy(template)
    else:
        cloned = _default_models_profile(target)
    for section in ("llm", "embedding"):
        section_cfg = cloned.setdefault(section, {})
        providers = section_cfg.setdefault("providers", {})
        for provider_id in PROVIDERS:
            provider_cfg = providers.setdefault(provider_id, {"type": provider_id})
            provider_cfg["api_key_ref"] = _key_ref(target, section, provider_id)
    profiles[target] = cloned


def _validate_config(config_dir: str | None) -> Tuple[bool, str]:
    try:
        models = load_models_config(config_dir)
        registry = default_registry()
        profiles = models.get("profiles", {})
        for profile_name, profile_cfg in profiles.items():
            for section in ("llm", "embedding"):
                if section not in profile_cfg:
                    return False, f"missing {profile_name}.{section}"
                active = profile_cfg[section].get("active")
                providers = profile_cfg[section].get("providers", {})
                if not active:
                    return False, f"missing active for {profile_name}.{section}"
                if active not in providers:
                    return False, f"missing provider config for {profile_name}.{section}.{active}"
                provider_type = providers[active].get("type", active)
                registry.create(provider_type, providers[active], provider_id=active)
        app_cfg = load_app_config(config_dir)
        active_profile = app_cfg.get("active_profile")
        app_profiles = app_cfg.get("profiles", {})
        if not active_profile or active_profile not in app_profiles:
            return False, "invalid active_profile"
    except Exception as exc:
        return False, str(exc)
    return True, "config ok"


def create_app(config_dir: str | None = None) -> FastAPI:
    app = FastAPI(title="Agent Control Panel")
    base_dir = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=base_dir / "templates")
    app.mount("/static", StaticFiles(directory=base_dir / "static"), name="static")
    paths = _config_paths(config_dir)

    # 初始化对话管理器
    conv_manager = ConversationManager(paths["base"])

    # 初始化记忆管理器（外层作用域，供 Memory API 端点使用）
    memory_manager = get_memory_manager()
    pending_tool_approvals: dict[str, dict[str, Any]] = {}
    app.state.pending_tool_approvals = pending_tool_approvals

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, profile: Optional[str] = None) -> HTMLResponse:
        models = load_models_config(config_dir)
        app_cfg = load_app_config(config_dir)
        active_profile = app_cfg.get("active_profile", "")
        edit_profile = profile or active_profile
        modal_target = request.query_params.get("modal", "")
        panel_target = request.query_params.get("panel", "")
        knowledge_bases = app_cfg.get("knowledge_bases", [])
        active_kbs = _active_kb_list(app_cfg)
        profile_names = sorted(app_cfg.get("profiles", {}).keys())
        profile_models = {n: _profile_active_llm_model(models, n) for n in profile_names}
        active_image_gen_model = _profile_active_image_gen_model(models, active_profile)
        valid, message = _validate_config(config_dir)
        return templates.TemplateResponse(
            "app.html",
            {
                "request": request,
                "active_profile": active_profile,
                "edit_profile": edit_profile,
                "modal_target": modal_target,
                "panel_target": panel_target,
                "knowledge_bases": knowledge_bases,
                "active_kbs": active_kbs,
                "profiles": profile_names,
                "profile_models": profile_models,
                "active_image_gen_model": active_image_gen_model,
                "status_ok": valid,
                "status_message": message,
            },
        )

    @app.get("/api/agent_runtime")
    async def api_agent_runtime():
        """Inspect the UI chat path and registered tool surfaces."""
        app_cfg = load_app_config(config_dir)
        active_profile = app_cfg.get("active_profile", "")
        models = load_models_config(config_dir)
        active_model = _profile_active_llm_model(models, active_profile)

        def config_loader():
            return load_app_config(config_dir)

        def llm_info_loader():
            provider, _, model = active_model.partition(" / ")
            return {
                "provider": provider or "unknown",
                "model": model or active_model or "unknown",
            }

        from ..tools.memory import create_memory_tools
        from ..core.runtime import RuntimeConfig
        from ..tools_v2.primitives import full_toolset

        legacy_tools = []
        legacy_tools.extend(create_kb_tools(config_loader, None, None))
        legacy_tools.extend(create_filesystem_tools(allowed_paths=None, max_file_size_mb=10.0))
        legacy_tools.extend(create_system_tools(llm_info_loader, config_loader))
        legacy_tools.extend(create_memory_tools(memory_manager))

        v2_tools = list(full_toolset().values())

        return {
            "ok": True,
            "active_profile": active_profile,
            "active_model": active_model,
            "active_kbs": _active_kb_list(app_cfg),
            "chat_endpoint": "/api/agent_chat_v2",
            "chat_executor": "AgentLoop",
            "ui_uses_agent_loop": True,
            "agent_loop_endpoint": "/api/agent_chat_v2",
            "agent_loop_mounted": True,
            "runtime": RuntimeConfig.from_app_config(app_cfg).to_metadata(),
            "legacy_tools": [_legacy_tool_info(t) for t in legacy_tools],
            "v2_tools": [_v2_tool_info(t) for t in v2_tools],
        }

    @app.post("/api/tool_approvals/{approval_id}")
    async def api_tool_approval(approval_id: str, request: Request):
        payload = await request.json()
        record = pending_tool_approvals.get(approval_id)
        if not record:
            return JSONResponse(
                {"ok": False, "error": "Approval request not found or expired."},
                status_code=404,
            )
        future = record.get("future")
        if future is not None and not future.done():
            future.set_result(bool(payload.get("approved")))
        return {"ok": True, "approval_id": approval_id}

    @app.post("/profiles/select")
    async def select_profile(request: Request):
        form = await request.form()
        profile_name = str(form.get("profile") or "")
        app_cfg = load_app_config(config_dir)
        profiles = app_cfg.get("profiles", {})
        if profile_name in profiles:
            app_cfg["active_profile"] = profile_name
            save_yaml(paths["app"], app_cfg)
        return RedirectResponse(f"/?profile={profile_name}&modal=config", status_code=303)

    @app.post("/api/profiles/create")
    async def api_profiles_create(request: Request):
        # Add-only profile creation. Persists the API key via keyring on the spot.
        # Embedding section intentionally omitted — modern chat models don't need it
        # and the UI no longer exposes it. image_gen is optional.
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON."}, status_code=400)

        name = _sanitize_profile_name(str(data.get("name") or ""))
        if not name:
            return JSONResponse({"ok": False, "error": "Invalid profile name."}, status_code=400)

        llm = data.get("llm") or {}
        vendor = str(llm.get("vendor") or "").strip()
        model = str(llm.get("model") or "").strip()
        api_key = str(llm.get("api_key") or "").strip()
        base_url = str(llm.get("base_url") or "").strip()
        if not vendor or not model or not api_key:
            return JSONResponse(
                {"ok": False, "error": "LLM vendor, model, and api_key are required."},
                status_code=400,
            )

        app_cfg = load_app_config(config_dir)
        profiles = app_cfg.setdefault("profiles", {})
        if name in profiles:
            return JSONResponse({"ok": False, "error": "Name already exists."}, status_code=400)

        profiles[name] = _default_profile_entry(name)
        app_cfg["active_profile"] = name
        save_yaml(paths["app"], app_cfg)
        _ensure_profile_assets(paths["base"].parent, name)

        llm_ref = _key_ref(name, "llm", vendor)
        llm_provider_cfg: dict = {
            "type": vendor,
            "model": model,
            "api_key_env": "",
            "api_key_ref": llm_ref,
        }
        if base_url:
            llm_provider_cfg["base_url"] = base_url

        models_cfg = load_models_config(config_dir)
        models_profiles = models_cfg.setdefault("profiles", {})
        models_profiles[name] = {
            "llm": {
                "active": vendor,
                "fallback": [vendor],
                "providers": {vendor: llm_provider_cfg},
                "vendor": vendor,
            },
        }
        store_api_key(llm_ref, api_key)

        image_gen = data.get("image_gen") or {}
        if image_gen:
            ig_vendor = str(image_gen.get("vendor") or "").strip()
            ig_model = str(image_gen.get("model") or "").strip()
            ig_key = str(image_gen.get("api_key") or "").strip()
            ig_base = str(image_gen.get("base_url") or "").strip()
            if ig_vendor and ig_model and ig_key:
                ig_ref = _key_ref(name, "image_gen", ig_vendor)
                ig_provider: dict = {
                    "type": ig_vendor,
                    "model": ig_model,
                    "api_key_env": "",
                    "api_key_ref": ig_ref,
                }
                if ig_base:
                    ig_provider["base_url"] = ig_base
                models_profiles[name]["image_gen"] = {
                    "active": ig_vendor,
                    "providers": {ig_vendor: ig_provider},
                }
                store_api_key(ig_ref, ig_key)

        save_yaml(paths["models"], models_cfg)
        return JSONResponse({"ok": True, "name": name})

    @app.post("/api/list_models")
    async def api_list_models(request: Request):
        # Side-effect-free: probes vendor's /v1/models (or equivalent) with the given
        # key and returns the list. Does NOT touch models.yaml or keyring — that's
        # the add/edit flow's responsibility. Failures come back as
        # {ok: false, error: str} with HTTP 200 so the UI can show a soft warning.
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "Invalid JSON."}, status_code=400)

        vendor = str(payload.get("vendor") or "").strip()
        key = str(payload.get("key") or "").strip()
        base_url_raw = payload.get("base_url")
        base_url = _normalize_base_url(str(base_url_raw)) if base_url_raw else ""
        section = str(payload.get("section") or "llm").strip() or "llm"

        if vendor in VENDOR_BASE_URLS and not base_url:
            base_url = _normalize_base_url(VENDOR_BASE_URLS[vendor])
        if not vendor:
            return JSONResponse({"ok": False, "error": "Vendor required."})
        if not key:
            return JSONResponse({"ok": False, "error": "API key required."})

        try:
            models = _list_models_with_key(vendor, key, base_url or None, section)
            return JSONResponse({
                "ok": True,
                "vendor": vendor,
                "models": models,
                "base_url": base_url,
            })
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)})

    @app.post("/kb/add")
    async def add_kb(request: Request):
        form = await request.form()
        raw_name = str(form.get("name") or "")
        raw_path = str(form.get("path") or "")
        name = _sanitize_profile_name(raw_name)
        if not name:
            return JSONResponse({"ok": False, "error": "Invalid name."}, status_code=400)
        if not raw_path:
            return JSONResponse({"ok": False, "error": "Missing path."}, status_code=400)

        app_cfg = load_app_config(config_dir)
        knowledge_bases = app_cfg.setdefault("knowledge_bases", [])
        if any(entry.get("name") == name for entry in knowledge_bases):
            return JSONResponse({"ok": False, "error": "Name already exists."}, status_code=400)

        base_dir = paths["base"].parent
        path = _normalize_kb_path(raw_path, base_dir)
        path.mkdir(parents=True, exist_ok=True)
        db_path = _kb_db_path(name, base_dir)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        knowledge_bases.append({"name": name, "path": str(path), "db_path": str(db_path)})
        active_kbs = _active_kb_list(app_cfg)
        if name not in active_kbs:
            active_kbs.append(name)
        _set_active_kbs(app_cfg, active_kbs)
        save_yaml(paths["app"], app_cfg)
        base_storage_dir = paths["base"].parent
        _index_kb_paths(config_dir, base_storage_dir, app_cfg, name, app_cfg.get("active_profile"))
        return RedirectResponse("/?modal=kb", status_code=303)

    @app.post("/kb/select")
    async def select_kb(request: Request):
        form = await request.form()
        name = str(form.get("name") or "")
        app_cfg = load_app_config(config_dir)
        knowledge_bases = app_cfg.get("knowledge_bases", [])
        if any(entry.get("name") == name for entry in knowledge_bases):
            active_kbs = _active_kb_list(app_cfg)
            was_active = name in active_kbs
            if was_active:
                active_kbs = [item for item in active_kbs if item != name]
            else:
                active_kbs.append(name)
            _set_active_kbs(app_cfg, active_kbs)
            save_yaml(paths["app"], app_cfg)
            if not was_active:
                base_storage_dir = paths["base"].parent
                _index_kb_paths(config_dir, base_storage_dir, app_cfg, name, app_cfg.get("active_profile"))
        return RedirectResponse("/?modal=kb", status_code=303)

    @app.post("/api/kb/open")
    async def api_kb_open(request: Request):
        payload = await request.json()
        name = str(payload.get("name") or "")
        if not name:
            return JSONResponse({"ok": False, "error": "Missing name."}, status_code=400)
        app_cfg = load_app_config(config_dir)
        entry = _find_kb_entry(app_cfg, name)
        if entry is None:
            return JSONResponse({"ok": False, "error": "Knowledge base not found."}, status_code=404)
        path = Path(entry.get("path", ""))
        if not path.exists():
            return JSONResponse({"ok": False, "error": "Path not found."}, status_code=404)
        try:
            _open_folder(path)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True})

    @app.post("/api/kb/upload")
    async def api_kb_upload(
        name: str = Form(...),
        files: list[UploadFile] = File(...),
    ):
        app_cfg = load_app_config(config_dir)
        entry = _find_kb_entry(app_cfg, name)
        if entry is None:
            return JSONResponse({"ok": False, "error": "Knowledge base not found."}, status_code=404)
        target_dir = Path(entry.get("path", ""))
        if not target_dir.exists():
            return JSONResponse({"ok": False, "error": "Path not found."}, status_code=404)
        saved: list[str] = []
        for upload in files:
            if not upload.filename:
                continue
            safe_name = _safe_filename(upload.filename)
            if not safe_name:
                continue
            dest = _unique_path(target_dir / safe_name)
            with dest.open("wb") as handle:
                shutil.copyfileobj(upload.file, handle)
            upload.file.close()
            saved.append(str(dest))
        if saved:
            base_storage_dir = paths["base"].parent
            target_paths = [Path(path) for path in saved]
            _index_kb_paths(
                config_dir,
                base_storage_dir,
                app_cfg,
                name,
                app_cfg.get("active_profile"),
                target_paths=target_paths,
            )
        return JSONResponse({"ok": True, "saved": len(saved), "files": saved})

    @app.post("/api/chat")
    async def api_chat(request: Request):
        start_time = perf_counter()
        payload = await request.json()
        message = str(payload.get("message") or "").strip()
        use_kb = bool(payload.get("use_kb", True))
        kb_mode = str(payload.get("kb_mode") or "auto").lower()
        if kb_mode not in ("auto", "force", "off"):
            kb_mode = "auto"
        if kb_mode == "off":
            use_kb = False
        if not message:
            return JSONResponse({"ok": False, "error": "Empty message."}, status_code=400)

        app_cfg = load_app_config(config_dir)
        active_profile = app_cfg.get("active_profile")
        active_kbs = _active_kb_list(app_cfg) if use_kb else []
        try:
            profile = resolve_profile(config_dir, active_profile)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        models_config = load_models_config(config_dir)
        registry = default_registry()
        try:
            llm = registry.create_from_config(models_config, "llm", profile=profile.name)
        except Exception as exc:
            log_event(
                profile.logs_dir,
                {
                    "action": "chat_error",
                    "profile": profile.name,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        behavior = resolve_behavior(config_dir, profile.name, llm.provider, llm.model)
        llm_kwargs = build_llm_kwargs(behavior)

        # Log chat request with provider/model info
        log_event(
            profile.logs_dir,
            {
                "action": "chat_request",
                "profile": profile.name,
                "provider": llm.provider,
                "model": llm.model,
                "active_kbs": active_kbs,
                "use_kb": use_kb,
                "kb_mode": kb_mode,
                "message_length": len(message),
            },
        )

        send_mode = "raw"
        mask_fn = None
        if profile.cloud_send == "masked":
            lexicon = load_lexicons(profile.lexicon_files)
            mask_fn = lambda text: mask_text(text, lexicon)
            send_mode = "masked"

        question = mask_fn(message) if mask_fn else message
        gate_start = perf_counter()
        skip_reason = ""
        skip_kb = False
        if kb_mode == "auto":
            skip_reason = _skip_kb_reason(question)
            skip_kb = bool(skip_reason) and skip_reason != "kb_hint"
        gate_ms = (perf_counter() - gate_start) * 1000.0
        sources: list[dict] = []
        kb_metrics: list[dict] = []
        used_context = False
        llm_ms = 0.0
        try:
            if active_kbs and not skip_kb:
                rag_config = _build_rag_config(config_dir, profile.name,
                    _kb_db_path(active_kbs[0], paths["base"].parent))
                all_results, kb_metrics = _kb_hybrid_search(
                    config_dir, profile.name, app_cfg, active_kbs, question,
                    paths["base"].parent, top_k=rag_config.top_k,
                )
                if all_results:
                    all_results.sort(key=lambda item: item.score, reverse=True)
                    results = all_results[: rag_config.top_k]
                    use_context = True
                    if kb_mode == "auto" and results:
                        min_score = max(rag_config.score_threshold, 0.35)
                        if results[0].score < min_score:
                            use_context = False
                    if use_context:
                        used_context = True
                        llm_start = perf_counter()
                        answer = answer_question(
                            llm=llm,
                            question=question,
                            results=results,
                            max_context_chars=rag_config.max_context_chars,
                            allow_empty=True,
                            mask_fn=mask_fn,
                            llm_kwargs=llm_kwargs,
                        )
                        llm_ms += (perf_counter() - llm_start) * 1000.0
                        sources = [
                            {
                                "path": item.metadata.get("source_path", ""),
                                "score": item.score,
                                "kb": item.metadata.get("kb_name", ""),
                            }
                            for item in results
                        ]
                        if answer.strip() == NO_CONTEXT_MESSAGE:
                            llm_start = perf_counter()
                            answer = llm.chat(question, **llm_kwargs)
                            llm_ms += (perf_counter() - llm_start) * 1000.0
                            sources = []
                    else:
                        llm_start = perf_counter()
                        answer = llm.chat(question, **llm_kwargs)
                        llm_ms += (perf_counter() - llm_start) * 1000.0
                else:
                    # No KB hits: fall back to normal LLM answer instead of returning "no context".
                    llm_start = perf_counter()
                    answer = llm.chat(question, **llm_kwargs)
                    llm_ms += (perf_counter() - llm_start) * 1000.0
            else:
                llm_start = perf_counter()
                answer = llm.chat(question, **llm_kwargs)
                llm_ms += (perf_counter() - llm_start) * 1000.0
        except Exception as exc:
            log_event(
                profile.logs_dir,
                {
                    "action": "chat_error",
                    "profile": profile.name,
                    "provider": llm.provider,
                    "model": llm.model,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        if sources:
            sources = _filter_sources_by_citations(answer, sources)

        total_ms = (perf_counter() - start_time) * 1000.0

        log_event(
            profile.logs_dir,
            {
                "action": "chat",
                "profile": profile.name,
                "provider": llm.provider,
                "model": llm.model,
                "active_kbs": active_kbs,
                "use_kb": use_kb,
                "skip_kb": skip_kb,
                "skip_kb_reason": skip_reason,
                "gate_ms": gate_ms,
                "kb_count": len(active_kbs),
                "kb_metrics": kb_metrics,
                "use_context": used_context,
                "sources_count": len(sources),
                "llm_ms": llm_ms,
                "total_ms": total_ms,
                "send_mode": send_mode,
                "status": "ok",
            },
        )
        return JSONResponse({
            "ok": True,
            "reply": answer,
            "sources": sources,
            "provider": llm.provider,
            "model": llm.model,
        })

    @app.post("/api/chat_stream")
    async def api_chat_stream(request: Request):
        """Legacy streaming endpoint (backward compatible)."""
        payload = await request.json()
        message = str(payload.get("message") or "").strip()
        use_kb = bool(payload.get("use_kb", True))
        kb_mode = str(payload.get("kb_mode") or "auto").lower()
        if kb_mode not in ("auto", "force", "off"):
            kb_mode = "auto"
        if kb_mode == "off":
            use_kb = False
        if not message:
            return JSONResponse({"ok": False, "error": "Empty message."}, status_code=400)

        app_cfg = load_app_config(config_dir)
        active_profile = app_cfg.get("active_profile")
        active_kbs = _active_kb_list(app_cfg) if use_kb else []
        try:
            profile = resolve_profile(config_dir, active_profile)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        models_config = load_models_config(config_dir)
        registry = default_registry()
        try:
            llm = registry.create_from_config(models_config, "llm", profile=profile.name)
        except Exception as exc:
            log_event(
                profile.logs_dir,
                {
                    "action": "chat_stream_error",
                    "profile": profile.name,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
            )
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        behavior = resolve_behavior(config_dir, profile.name, llm.provider, llm.model)
        llm_kwargs = build_llm_kwargs(behavior)

        log_event(
            profile.logs_dir,
            {
                "action": "chat_stream_request",
                "profile": profile.name,
                "provider": llm.provider,
                "model": llm.model,
                "active_kbs": active_kbs,
                "use_kb": use_kb,
                "kb_mode": kb_mode,
                "message_length": len(message),
            },
        )

        send_mode = "raw"
        mask_fn = None
        if profile.cloud_send == "masked":
            lexicon = load_lexicons(profile.lexicon_files)
            mask_fn = lambda text: mask_text(text, lexicon)
            send_mode = "masked"

        question = mask_fn(message) if mask_fn else message
        skip_kb = kb_mode == "auto" and _should_skip_kb(question)

        def event_stream():
            sources: list[dict] = []
            try:
                if active_kbs and not skip_kb:
                    rag_config = _build_rag_config(config_dir, profile.name,
                        _kb_db_path(active_kbs[0], paths["base"].parent))
                    all_results, _ = _kb_hybrid_search(
                        config_dir, profile.name, app_cfg, active_kbs, question,
                        paths["base"].parent, top_k=rag_config.top_k,
                    )
                    if all_results:
                        results = all_results[: rag_config.top_k]
                        use_context = True
                        if kb_mode == "auto" and results:
                            min_score = max(rag_config.score_threshold, 0.35)
                            if results[0].score < min_score:
                                use_context = False
                        if use_context:
                            context = build_context(results, max_context_chars=rag_config.max_context_chars)
                            if mask_fn and context:
                                context = mask_fn(context)
                            if context:
                                prompt = build_prompt(question, context)
                                sources = [
                                    {
                                        "path": item.metadata.get("source_path", ""),
                                        "score": item.score,
                                        "kb": item.metadata.get("kb_name", ""),
                                    }
                                    for item in results
                                ]
                                stream_fn = getattr(llm, "chat_stream", None)
                                if stream_fn is None:
                                    answer = llm.chat(prompt, **llm_kwargs)
                                    if sources:
                                        sources = _filter_sources_by_citations(answer, sources)
                                    yield f"data: {json.dumps({'delta': answer, 'sources': sources, 'done': True, 'provider': llm.provider, 'model': llm.model}, ensure_ascii=False)}\n\n"
                                    yield "data: [DONE]\n\n"
                                    return
                                full_answer = ""
                                for chunk in stream_fn(prompt, **llm_kwargs):
                                    if not chunk:
                                        continue
                                    full_answer += chunk
                                    yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                                if sources:
                                    sources = _filter_sources_by_citations(full_answer, sources)
                                yield f"data: {json.dumps({'done': True, 'sources': sources, 'provider': llm.provider, 'model': llm.model}, ensure_ascii=False)}\n\n"
                                yield "data: [DONE]\n\n"
                                return

                stream_fn = getattr(llm, "chat_stream", None)
                if stream_fn is None:
                    answer = llm.chat(question, **llm_kwargs)
                    yield f"data: {json.dumps({'delta': answer, 'sources': sources, 'done': True, 'provider': llm.provider, 'model': llm.model}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                for chunk in stream_fn(question, **llm_kwargs):
                    if not chunk:
                        continue
                    yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True, 'sources': sources, 'provider': llm.provider, 'model': llm.model}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                err = str(exc)
                log_event(
                    profile.logs_dir,
                    {
                        "action": "chat_stream_error",
                        "profile": profile.name,
                        "provider": llm.provider,
                        "model": llm.model,
                        "error": err,
                        "traceback": traceback.format_exc(),
                    },
                )
                yield f"data: {json.dumps({'error': err, 'done': True}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        log_event(
            profile.logs_dir,
            {
                "action": "chat_stream",
                "profile": profile.name,
                "provider": llm.provider,
                "model": llm.model,
                "active_kbs": active_kbs,
                "use_kb": use_kb,
                "send_mode": send_mode,
                "status": "ok",
            },
        )
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/chat_stream_v2")
    async def api_chat_stream_v2(request: Request):
        """New streaming endpoint with Activity events (sync generator version)."""
        payload = await request.json()
        message = str(payload.get("message") or "").strip()
        use_kb = bool(payload.get("use_kb", True))
        kb_mode = str(payload.get("kb_mode") or "auto").lower()
        history = payload.get("history") or []  # 新增：获取历史对话
        if kb_mode not in ("auto", "force", "off"):
            kb_mode = "auto"
        if kb_mode == "off":
            use_kb = False
        if not message:
            return JSONResponse({"ok": False, "error": "Empty message."}, status_code=400)

        app_cfg = load_app_config(config_dir)
        active_profile = app_cfg.get("active_profile")
        active_kbs = _active_kb_list(app_cfg) if use_kb else []
        try:
            profile = resolve_profile(config_dir, active_profile)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        models_config = load_models_config(config_dir)
        registry = default_registry()
        try:
            llm = registry.create_from_config(models_config, "llm", profile=profile.name)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        behavior = resolve_behavior(config_dir, profile.name, llm.provider, llm.model)
        llm_kwargs = build_llm_kwargs(behavior)

        send_mode = "raw"
        mask_fn = None
        if profile.cloud_send == "masked":
            lexicon = load_lexicons(profile.lexicon_files)
            mask_fn = lambda text: mask_text(text, lexicon)
            send_mode = "masked"

        question = mask_fn(message) if mask_fn else message
        skip_reason = _skip_kb_reason(question) if kb_mode == "auto" else ""
        skip_kb = bool(skip_reason) and skip_reason != "kb_hint"

        # Helper to format SSE
        def format_sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        def event_stream_v2():
            """Sync generator that yields SSE events in sequence."""
            request_id = str(uuid.uuid4())[:8]
            start_time = perf_counter()
            sources: list[dict] = []

            try:
                # 1. Intent Recognition
                yield format_sse("activity", {
                    "id": f"{request_id}_intent",
                    "type": "intent",
                    "title": "Intent Recognition",
                    "detail": f"use_kb={use_kb}, kb_mode={kb_mode}, skip={skip_kb}",
                    "status": "done",
                    "ts": perf_counter() * 1000,
                    "meta": {"use_kb": use_kb, "kb_mode": kb_mode, "skip_kb": skip_kb}
                })

                prompt = question

                # 2. KB Search (if needed)
                if active_kbs and not skip_kb:
                    yield format_sse("activity", {
                        "id": f"{request_id}_kb",
                        "type": "kb_search_start",
                        "title": "KB Search",
                        "detail": f"query={question[:30]}...",
                        "status": "start",
                        "ts": perf_counter() * 1000,
                        "meta": {}
                    })

                    kb_start = perf_counter()
                    rag_config = _build_rag_config(config_dir, profile.name,
                        _kb_db_path(active_kbs[0], paths["base"].parent))
                    all_results, _ = _kb_hybrid_search(
                        config_dir, profile.name, app_cfg, active_kbs, question,
                        paths["base"].parent, top_k=rag_config.top_k,
                    )

                    kb_ms = int((perf_counter() - kb_start) * 1000)
                    hits = len(all_results)

                    yield format_sse("activity", {
                        "id": f"{request_id}_kb",
                        "type": "kb_search_done",
                        "title": "KB Search Done",
                        "detail": f"hits={hits}, {kb_ms}ms",
                        "status": "done",
                        "ts": perf_counter() * 1000,
                        "meta": {"hits": hits, "latency_ms": kb_ms}
                    })

                    if all_results and rag_config:
                        all_results.sort(key=lambda item: item.score, reverse=True)
                        results = all_results[: rag_config.top_k]
                        use_context = True
                        if kb_mode == "auto" and results:
                            min_score = max(rag_config.score_threshold, 0.35)
                            if results[0].score < min_score:
                                use_context = False
                        if use_context:
                            context = build_context(results, max_context_chars=rag_config.max_context_chars)
                            if mask_fn and context:
                                context = mask_fn(context)
                            if context:
                                prompt = build_prompt(question, context)
                                sources = [
                                    {
                                        "path": item.metadata.get("source_path", ""),
                                        "score": item.score,
                                        "kb": item.metadata.get("kb_name", ""),
                                    }
                                    for item in results
                                ]
                else:
                    yield format_sse("activity", {
                        "id": f"{request_id}_kb",
                        "type": "kb_skip",
                        "title": "KB Skipped",
                        "detail": f"reason={skip_reason or 'disabled'}",
                        "status": "done",
                        "ts": perf_counter() * 1000,
                        "meta": {}
                    })

                # 3. LLM Generation
                # 构建消息列表（支持历史对话）
                messages = []
                for item in history:
                    role = item.get("role", "")
                    content = item.get("content", "")
                    if role and content:
                        messages.append({"role": role, "content": content})
                messages.append({"role": "user", "content": prompt})

                # 将messages传入llm_kwargs
                if messages:
                    llm_kwargs["messages"] = messages

                llm_start = perf_counter()
                stream_fn = getattr(llm, "chat_stream", None)
                full_answer = ""
                reasoning_buffer = ""
                token_count = 0
                thinking_started = False
                generating_started = False

                if stream_fn is None:
                    full_answer = llm.chat(prompt, **llm_kwargs)
                    yield format_sse("token", {"text": full_answer})
                    token_count = len(full_answer)
                else:
                    for chunk in stream_fn(prompt, **llm_kwargs):
                        if not chunk:
                            continue

                        # 处理结构化响应（zhipu格式）
                        if isinstance(chunk, dict):
                            chunk_type = chunk.get("type")
                            text = chunk.get("text", "")

                            if chunk_type == "reasoning":
                                # 推理内容 → Activity事件
                                if not thinking_started:
                                    thinking_started = True
                                    yield format_sse("activity", {
                                        "id": f"{request_id}_thinking",
                                        "type": "thinking_start",
                                        "title": "Thinking",
                                        "detail": "",
                                        "status": "start",
                                        "ts": perf_counter() * 1000,
                                        "meta": {}
                                    })

                                reasoning_buffer += text
                                # 实时更新Activity中的推理内容
                                yield format_sse("activity", {
                                    "id": f"{request_id}_thinking",
                                    "type": "thinking_update",
                                    "title": "Thinking",
                                    "detail": reasoning_buffer,
                                    "status": "progress",
                                    "ts": perf_counter() * 1000,
                                    "meta": {}
                                })

                            elif chunk_type == "content":
                                # 完成推理阶段
                                if thinking_started and not generating_started:
                                    yield format_sse("activity", {
                                        "id": f"{request_id}_thinking",
                                        "type": "thinking_done",
                                        "title": "Thinking Complete",
                                        "detail": reasoning_buffer,
                                        "status": "done",
                                        "ts": perf_counter() * 1000,
                                        "meta": {"reasoning_length": len(reasoning_buffer)}
                                    })

                                    # 开始生成阶段
                                    generating_started = True
                                    yield format_sse("activity", {
                                        "id": f"{request_id}_generating",
                                        "type": "generating_start",
                                        "title": "Generating Answer",
                                        "detail": "",
                                        "status": "start",
                                        "ts": perf_counter() * 1000,
                                        "meta": {}
                                    })

                                # 回答内容 → token事件（显示在聊天区）
                                full_answer += text
                                token_count += 1
                                yield format_sse("token", {"text": text})

                        # 兼容旧格式（纯字符串，其他provider）
                        else:
                            if not generating_started:
                                generating_started = True
                                yield format_sse("activity", {
                                    "id": f"{request_id}_generating",
                                    "type": "generating_start",
                                    "title": "Generating",
                                    "detail": f"model={llm.model}",
                                    "status": "start",
                                    "ts": perf_counter() * 1000,
                                    "meta": {}
                                })

                            full_answer += chunk
                            token_count += 1
                            yield format_sse("token", {"text": chunk})

                llm_ms = int((perf_counter() - llm_start) * 1000)

                # 完成生成
                if generating_started:
                    yield format_sse("activity", {
                        "id": f"{request_id}_generating",
                        "type": "generating_done",
                        "title": "Answer Complete",
                        "detail": f"tokens={token_count}, {llm_ms}ms",
                        "status": "done",
                        "ts": perf_counter() * 1000,
                        "meta": {"tokens": token_count, "latency_ms": llm_ms}
                    })

                # Filter sources by citations
                if sources:
                    sources = _filter_sources_by_citations(full_answer, sources)

                # 4. Done
                total_ms = int((perf_counter() - start_time) * 1000)
                yield format_sse("done", {
                    "total_time_ms": total_ms,
                    "sources": sources,
                    "provider": llm.provider,
                    "model": llm.model,
                })

            except Exception as exc:
                yield format_sse("activity", {
                    "id": f"{request_id}_error",
                    "type": "error",
                    "title": "Error",
                    "detail": str(exc),
                    "status": "error",
                    "ts": perf_counter() * 1000,
                    "meta": {}
                })
                total_ms = int((perf_counter() - start_time) * 1000)
                yield format_sse("done", {
                    "total_time_ms": total_ms,
                    "error": str(exc),
                    "sources": [],
                    "provider": llm.provider if llm else "",
                    "model": llm.model if llm else "",
                })
                log_event(
                    profile.logs_dir,
                    {
                        "action": "chat_stream_v2_error",
                        "profile": profile.name,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )

        return StreamingResponse(
            event_stream_v2(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/agent_chat")
    async def api_agent_chat(request: Request):
        """
        Agent模式聊天API - 支持Tool Use

        模型可以主动调用工具（如搜索知识库），然后继续推理。
        """
        payload = await request.json()
        message = str(payload.get("message") or "").strip()
        history = payload.get("history") or []
        images = payload.get("images") or []  # 前端发送的图片列表
        conv_id = payload.get("conversation_id")  # 可选，用于记忆上下文注入

        if not message and not images:
            return JSONResponse({"ok": False, "error": "Empty message."}, status_code=400)

        app_cfg = load_app_config(config_dir)
        active_profile = app_cfg.get("active_profile")

        try:
            profile = resolve_profile(config_dir, active_profile)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        models_config = load_models_config(config_dir)
        registry = default_registry()
        try:
            llm = registry.create_from_config(models_config, "llm", profile=profile.name)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

        behavior = resolve_behavior(config_dir, profile.name, llm.provider, llm.model)
        llm_kwargs = build_llm_kwargs(behavior)

        # 读取当前激活的知识库列表
        active_kbs = _active_kb_list(app_cfg)

        # 初始化KB工具
        def config_loader():
            return load_app_config(config_dir)

        def rag_service_builder(kb_name: str):
            entry = _find_kb_entry(app_cfg, kb_name)
            if not entry:
                return None
            db_path_value = entry.get("db_path")
            if db_path_value:
                db_path = Path(db_path_value)
            else:
                base_dir = paths["base"].parent
                db_path = _kb_db_path(kb_name, base_dir)
            return _build_rag_service(config_dir, profile.name, db_path)

        # 注册KB工具到全局Registry
        tool_registry = get_registry()
        tool_registry.clear()  # 清空后重新注册

        # 创建 KnowledgeManager（混合检索策略，接入 sqlite-vec）
        _km_vec_store = None
        _km_embedder = None
        try:
            models_config = load_models_config(config_dir)
            _km_embedder = default_registry().create_from_config(
                models_config, "embedding", profile=profile.name)
            _km_vec_store = _try_build_vec_store(
                _km_embedder,
                _kb_db_path(active_kbs[0], paths["base"].parent) if active_kbs else
                    paths["base"].parent / "data" / "default" / "rag.sqlite",
            )
        except Exception:
            pass  # 降级：无 embedder 或 sqlite-vec 不可用
        knowledge_manager = KnowledgeManager(
            vec_store=_km_vec_store,
            embedder=_km_embedder,
        )

        # 注册知识库工具（使用混合策略：小KB用Context Packing，大KB用RAG）
        kb_tools = create_kb_tools(config_loader, rag_service_builder, knowledge_manager)
        for tool in kb_tools:
            tool_registry.register(tool)

        # 注册文件系统工具（支持多模态图片读取）
        # allowed_paths 可以限制访问范围，None 表示不限制
        fs_tools = create_filesystem_tools(allowed_paths=None, max_file_size_mb=10.0)
        for tool in fs_tools:
            tool_registry.register(tool)

        # 注册系统配置工具（让模型能查询自己的配置信息）
        def llm_info_loader():
            return {
                "provider": llm.provider,
                "model": llm.model,
                "temperature": llm_kwargs.get("temperature"),
                "thinking_enabled": llm_kwargs.get("extra_body", {}).get("thinking", {}).get("type") == "enabled",
            }

        system_tools = create_system_tools(llm_info_loader, config_loader)
        for tool in system_tools:
            tool_registry.register(tool)

        # 注册记忆工具（让 Agent 能主动记住用户信息）
        from ..tools.memory import create_memory_tools
        memory_tools = create_memory_tools(memory_manager)
        for tool in memory_tools:
            tool_registry.register(tool)

        # 获取当前 LLM provider 类型（用于多模态消息格式）
        registered_tool_names = tool_registry.list_names()
        provider_type = llm.provider

        # 创建 Context Compactor（参考 Claude Code 的无限上下文机制）
        # 从配置读取压缩参数，或使用默认值
        compaction_cfg = app_cfg.get("agent", {}).get("compaction", {})
        compactor = create_compactor(
            enabled=compaction_cfg.get("enabled", True),
            token_threshold=compaction_cfg.get("token_threshold", 100_000),
            trigger_ratio=compaction_cfg.get("trigger_ratio", 0.75),
            summary_model=compaction_cfg.get("summary_model"),  # None = 使用主模型
        )

        # 创建AgentExecutor
        # max_iterations: 0=无限制(Claude Code模式)，可在app.yaml中配置 agent.max_iterations
        max_iter = app_cfg.get("agent", {}).get("max_iterations", 0)
        agent_config = AgentConfig(
            max_iterations=max_iter,
            enable_reasoning=True,
            provider=provider_type,  # 传入provider类型，用于多模态消息格式
            enable_compaction=compaction_cfg.get("enabled", True),
        )
        agent = AgentExecutor(llm, config=agent_config, compactor=compactor)

        # 构建system prompt - 简洁版，工具描述由 tools API 自动提供
        base_system_prompt = """你是一个智能助手。你有一组工具可以使用，包括知识库搜索、文件读取、系统配置查询、记忆管理等。

使用工具时：
- 需要查阅资料时，使用知识库工具搜索
- 需要看图片/文件时，使用文件读取工具
- 获得工具结果后，基于结果回答用户
- 发现用户的重要偏好或信息时，使用 remember_fact 工具记住

**重要**：如果用户询问关于你自己的信息（例如"你是什么模型"、"你用的什么配置"、"系统信息"等），
请使用 get_system_config 工具查询实际配置，然后如实回答。不要猜测或编造模型信息。

如果问题不需要工具（闲聊、常识），直接回答即可。"""

        # 注入用户记忆上下文（参考 GPT Bio Tool 设计）
        memory_context = memory_manager.get_context_injection(conv_id=conv_id)
        if memory_context:
            system_prompt = f"{base_system_prompt}\n\n{memory_context}"
        else:
            system_prompt = base_system_prompt

        # 构建历史消息
        messages = []
        for item in history:
            role = item.get("role", "")
            content = item.get("content", "")
            if role and content:
                messages.append({"role": role, "content": content})

        def format_sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        async def agent_event_stream():
            request_id = str(uuid.uuid4())[:8]
            start_time = perf_counter()

            try:
                yield format_sse("activity", {
                    "id": f"{request_id}_agent",
                    "type": "agent_start",
                    "title": "Agent Started",
                    "detail": f"model={llm.model}",
                    "status": "start",
                    "ts": perf_counter() * 1000,
                    "meta": {}
                })

                yield format_sse("activity", {
                    "id": f"{request_id}_tools",
                    "type": "tool_manifest",
                    "title": f"Tools loaded: {len(registered_tool_names)}",
                    "detail": ", ".join(registered_tool_names),
                    "status": "done",
                    "ts": perf_counter() * 1000,
                    "meta": {
                        "endpoint": "/api/agent_chat",
                        "executor": "AgentExecutor",
                        "tools": registered_tool_names,
                    }
                })

                # 移除llm_kwargs中的system_prompt，使用Agent专用的
                agent_kwargs = {k: v for k, v in llm_kwargs.items() if k != "system_prompt"}

                # 构建包含图片的 prompt（如果有图片的话）
                user_prompt = message
                attached_images = None
                if images:
                    attached_images = [
                        {
                            "base64": img.get("base64"),
                            "media_type": img.get("media_type", "image/png"),
                        }
                        for img in images
                        if img.get("base64")
                    ]
                    if attached_images:
                        user_prompt = f"{message}\n\n[用户附带了 {len(attached_images)} 张图片]" if message else f"[用户附带了 {len(attached_images)} 张图片，请描述图片内容]"

                async for event in agent.run_stream(
                    prompt=user_prompt,
                    messages=messages if messages else None,
                    system_prompt=system_prompt,
                    attached_images=attached_images,  # 传递附带的图片
                    **agent_kwargs
                ):
                    event_type = event.get("type")

                    if event_type == "reasoning":
                        yield format_sse("activity", {
                            "id": f"{request_id}_thinking",
                            "type": "thinking_update",
                            "title": "Thinking",
                            "detail": event.get("text", ""),
                            "status": "progress",
                            "ts": perf_counter() * 1000,
                            "meta": {}
                        })

                    elif event_type == "content":
                        yield format_sse("token", {"text": event.get("text", "")})

                    elif event_type == "tool_call":
                        data = event.get("data", {})
                        yield format_sse("activity", {
                            "id": f"{request_id}_tool_{data.get('id', '')}",
                            "type": "tool_call",
                            "title": f"Calling: {data.get('name', '')}",
                            "detail": json.dumps(data.get("arguments", {}), ensure_ascii=False),
                            "status": "start",
                            "ts": perf_counter() * 1000,
                            "meta": data
                        })

                    elif event_type == "tool_result":
                        data = event.get("data", {})
                        result = data.get("result", {})
                        yield format_sse("activity", {
                            "id": f"{request_id}_tool_{data.get('tool_call_id', '')}",
                            "type": "tool_result",
                            "title": f"Result: {data.get('name', '')}",
                            "detail": json.dumps(result.get("data", result.get("error", "")), ensure_ascii=False)[:200],
                            "status": "done" if result.get("success") else "error",
                            "ts": perf_counter() * 1000,
                            "meta": result
                        })

                    elif event_type == "done":
                        pass

                    elif event_type == "error":
                        yield format_sse("activity", {
                            "id": f"{request_id}_error",
                            "type": "error",
                            "title": "Error",
                            "detail": event.get("message", "Unknown error"),
                            "status": "error",
                            "ts": perf_counter() * 1000,
                            "meta": {}
                        })

                total_ms = int((perf_counter() - start_time) * 1000)
                yield format_sse("done", {
                    "total_time_ms": total_ms,
                    "sources": [],
                    "provider": llm.provider,
                    "model": llm.model,
                })

            except Exception as exc:
                yield format_sse("activity", {
                    "id": f"{request_id}_error",
                    "type": "error",
                    "title": "Agent Error",
                    "detail": str(exc),
                    "status": "error",
                    "ts": perf_counter() * 1000,
                    "meta": {}
                })
                total_ms = int((perf_counter() - start_time) * 1000)
                yield format_sse("done", {
                    "total_time_ms": total_ms,
                    "error": str(exc),
                    "sources": [],
                    "provider": llm.provider,
                    "model": llm.model,
                })
                log_event(
                    profile.logs_dir,
                    {
                        "action": "agent_chat_error",
                        "profile": profile.name,
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )

        return StreamingResponse(
            agent_event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------ #
    # v2: AgentLoop-powered chat endpoint.
    # Runs in parallel to /api/agent_chat while the legacy AgentExecutor
    # is still production. Single provider (OpenAI) for now; no multimodal,
    # no compactor, no legacy memory_manager injection — those land as
    # separate steps. SSE event vocabulary kept compatible with the frontend
    # (`token` / `activity` / `done`) so the UI can point at either.
    # ------------------------------------------------------------------ #
    @app.post("/api/agent_chat_v2")
    async def api_agent_chat_v2(request: Request):
        from ..core.loop import (
            AgentLoop,
            Hooks,
            ImageBlock,
            LoopConfig,
            Message,
            ReasoningDelta,
            Role,
            TextBlock,
            TextDelta,
            ToolResultBlock,
            ToolUseBlock,
        )
        from ..core.hooks import (
            make_approval_hook,
            make_acceptance_summary_hook,
            make_final_guard_hook,
            make_intent_without_action_hook,
        )
        from ..core.runtime import (
            RuntimeConfig,
            SessionMetadata,
            build_agent_system_prompt,
        )
        from ..core.compactor import ConversationCompactor

        payload = await request.json()
        request_id = str(uuid.uuid4())[:8]
        message = str(payload.get("message") or "").strip()
        history = payload.get("history") or []
        images = payload.get("images") or []
        conversation_id = str(payload.get("conversation_id") or "").strip() or None
        if not message:
            return JSONResponse({"ok": False, "error": "Empty message."}, status_code=400)

        app_cfg = load_app_config(config_dir)
        models_cfg = load_models_config(config_dir)
        active_profile = str(app_cfg.get("active_profile") or "")
        provider_name, provider_cfg = _profile_active_llm_provider(models_cfg, active_profile)
        provider_type = str(provider_cfg.get("type") or provider_name or "openai")
        model_name = str(
            payload.get("model")
            or provider_cfg.get("model")
            or os.getenv("OPENAI_MODEL")
            or "gpt-5.4-mini"
        )
        api_key = resolve_api_key(
            api_key_env=provider_cfg.get("api_key_env") or None,
            api_key_ref=provider_cfg.get("api_key_ref") or None,
        )
        if not api_key:
            return JSONResponse(
                {
                    "ok": False,
                    "error": (
                        f"API key not configured for active profile "
                        f"{active_profile!r} provider {provider_name!r}."
                    ),
                },
                status_code=400,
            )
        base_url = provider_cfg.get("base_url") or None

        try:
            adapter = _create_agent_loop_adapter(
                provider_type,
                model_name,
                api_key,
                base_url=base_url,
            )
        except Exception as exc:
            return JSONResponse(
                {
                    "ok": False,
                    "error": str(exc),
                },
                status_code=400,
            )
        active_kbs = _active_kb_list(app_cfg)
        tools, capability_scope = _select_v2_tools_for_turn(message, images, app_cfg)
        runtime_cfg = RuntimeConfig.from_app_config(app_cfg)
        session_metadata = SessionMetadata(
            session_id=request_id,
            conversation_id=conversation_id,
            endpoint="/api/agent_chat_v2",
            executor="AgentLoop",
            profile=active_profile,
            provider=provider_name or provider_type,
            provider_type=provider_type,
            model=model_name,
            active_kbs=tuple(active_kbs),
            tool_names=tuple(sorted(tools)),
            cwd=str(Path.cwd()),
            runtime=runtime_cfg.to_metadata(),
        )
        base_agent_prompt = (
            "You are a Claude-Code-style coding agent. Use tools when they are "
            "needed to inspect files, run commands, search knowledge, edit code, "
            "or verify work. For direct questions already answered by the "
            "session metadata, answer directly. Be concise after tool work.\n"
            "The runtime progressively exposes capabilities. Only use tools "
            "available in this turn; do not claim hidden tools are available.\n"
            "When you create or modify code, UI, documents, or other artifacts, "
            "first create or modify the exact requested target. If the user "
            "gave an output path and it does not exist, use Write to create it; "
            "do not substitute an older similar file unless explicitly asked. "
            "Verification happens after the write: read back written files, run "
            "targeted checks or smoke tests when practical, and fix discovered "
            "issues before the final answer. For HTML, CSS, JavaScript, browser "
            "UI, or game artifacts, call Verify with concrete browser assertions "
            "after writing and reading the file, then fix any failed assertion. "
            "For PDF, DOCX, XLSX, PPTX, or document layout questions, use "
            "RenderDocument to create page images before judging visual layout. "
            "For Excel workbook edits, inspect the workbook with ExcelRead "
            "before ExcelEdit; use explicit sheet/cell/range scopes and avoid "
            "global changes unless the user explicitly requested them. "
            "For Word document edits, inspect the document with WordRead "
            "before WordEdit; prefer paragraph_index-scoped operations and "
            "avoid global replacement unless the user explicitly requested it. "
            "For existing screenshots, generated images, or local visual "
            "details, use RenderDocument regions as a movable magnifier instead "
            "of regenerating the whole artifact. "
            "When rendered screenshots are attached after tool results, inspect "
            "them as visual evidence before deciding whether the artifact is ready. "
            "In auto mode, do not ask the user "
            "whether to proceed with the requested creation unless blocked by "
            "missing required information or a permission failure. If "
            "verification is not possible with the exposed tools, say so briefly."
        )
        memory_context = memory_manager.get_context_injection(conv_id=conversation_id)
        if memory_context:
            base_agent_prompt = (
                f"{base_agent_prompt}\n\n"
                "<user_facts>\n"
                f"{memory_context}\n"
                "</user_facts>"
            )

        approval_mode = str(payload.get("mode") or "confirm").lower()
        stream_queue: asyncio.Queue[tuple[str, dict] | None] = asyncio.Queue()
        trace_events: list[dict] = []
        assistant_token_chunks: list[str] = []
        safe_conv_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", conversation_id or "no-conv")
        safe_request_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", request_id)
        trace_path = (
            paths["base"].parent
            / "logs"
            / (active_profile or "default")
            / "traces"
            / safe_conv_id
            / f"{safe_request_id}.jsonl"
        )

        async def emit(event_name: str, data: dict) -> None:
            trace_events.append({
                "event": event_name,
                "data": data,
            })
            if event_name == "token":
                assistant_token_chunks.append(str(data.get("text") or ""))
            await stream_queue.put((event_name, data))

        async def approval_prompter(use: ToolUseBlock, ctx) -> bool:
            if approval_mode == "auto":
                return True
            if approval_mode == "read":
                return True
            approval_id = f"{request_id}_{use.id}"
            future = asyncio.get_running_loop().create_future()
            pending_tool_approvals[approval_id] = {
                "future": future,
                "tool": use.name,
                "input": use.input,
                "created_at": perf_counter(),
            }
            await emit("activity", {
                "id": f"{request_id}_approval_{use.id}",
                "type": "approval_request",
                "title": f"Approve: {use.name}",
                "detail": _summarize_tool_input(use.name, use.input),
                "status": "wait",
                "ts": perf_counter() * 1000,
                "meta": {
                    "approval_id": approval_id,
                    "tool": use.name,
                    "input_summary": _summarize_tool_input(use.name, use.input),
                },
            })
            timeout = float(
                ((app_cfg.get("runtime") or {}).get("approval_timeout_seconds") or 300)
            )
            try:
                return bool(await asyncio.wait_for(future, timeout=timeout))
            except asyncio.TimeoutError:
                return False
            finally:
                pending_tool_approvals.pop(approval_id, None)

        hooks = Hooks(
            on_stop=[
                make_intent_without_action_hook(),
                make_final_guard_hook(),
                make_acceptance_summary_hook(),
            ],
            pre_tool_use=[make_approval_hook(tools, approval_prompter)],
        )

        class _CompactionSummaryLLM:
            def __init__(self, model_adapter):
                self.model_adapter = model_adapter

            async def chat(self, messages: list[dict], **kwargs) -> dict:
                system_parts: list[str] = []
                internal_messages: list[Message] = []
                for msg in messages:
                    role = str(msg.get("role") or "user").lower()
                    content = str(msg.get("content") or "")
                    if not content:
                        continue
                    if role == "system":
                        system_parts.append(content)
                    elif role == "assistant":
                        internal_messages.append(
                            Message(role=Role.ASSISTANT, content=[TextBlock(text=content)])
                        )
                    else:
                        internal_messages.append(
                            Message(role=Role.USER, content=[TextBlock(text=content)])
                        )
                chunks: list[str] = []
                async for delta in self.model_adapter.stream(
                    internal_messages,
                    tools=[],
                    system="\n\n".join(system_parts) or None,
                    **kwargs,
                ):
                    if isinstance(delta, TextDelta) and delta.text:
                        chunks.append(delta.text)
                return {"content": "".join(chunks)}

        history_for_loop = list(history)
        compaction_cfg = (app_cfg.get("agent") or {}).get("compaction") or {}
        compactor = ConversationCompactor(CompactionConfig(
            enabled=bool(compaction_cfg.get("enabled", True)),
            token_threshold=int(compaction_cfg.get("token_threshold") or 100_000),
            trigger_ratio=float(compaction_cfg.get("trigger_ratio") or 0.75),
            protected_recent_messages=int(
                compaction_cfg.get("protected_recent_messages") or 10
            ),
            protected_recent_tokens=int(
                compaction_cfg.get("protected_recent_tokens") or 20_000
            ),
            summary_model=compaction_cfg.get("summary_model") or None,
            summary_max_tokens=int(compaction_cfg.get("summary_max_tokens") or 2000),
        ))
        if compactor.should_compact(history_for_loop):
            result = await compactor.compact(
                history_for_loop,
                _CompactionSummaryLLM(adapter),
            )
            if result.success and result.summary:
                history_for_loop = compactor.apply_compaction(history_for_loop, result)

        prior: list[Message] = []
        for item in history_for_loop:
            role_raw = str(item.get("role", "")).lower()
            text = str(item.get("content") or "")
            if not text:
                continue
            if role_raw == "user":
                prior.append(Message(role=Role.USER, content=[TextBlock(text=text)]))
            elif role_raw == "assistant":
                prior.append(Message(role=Role.ASSISTANT, content=[TextBlock(text=text)]))

        image_blocks: list[ImageBlock] = []
        for img in images:
            if not isinstance(img, dict):
                continue
            base64_data = str(img.get("base64") or "").strip()
            if not base64_data:
                continue
            image_blocks.append(ImageBlock(
                base64=base64_data,
                media_type=str(img.get("media_type") or "image/png"),
                name=str(img.get("name") or ""),
            ))

        loop = AgentLoop(
            adapter=adapter,
            tools=tools,
            hooks=hooks,
            config=LoopConfig(
                max_iterations=int(payload.get("max_iterations") or 20),
                parallel_tool_calls=True,
                permission_mode=(
                    "plan" if str(payload.get("mode") or "").lower() == "read"
                    else "default"
                ),
                trace_path=trace_path,
                system_prompt=build_agent_system_prompt(
                    base_agent_prompt,
                    session_metadata,
                ),
            ),
        )

        def fmt(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        async def produce():
            rid = request_id
            t0 = perf_counter()
            persisted_error: str | None = None

            def _loop_trace_records() -> list[dict]:
                if not trace_path.exists():
                    return []
                records: list[dict] = []
                for line in trace_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        records.append({"raw": line})
                return records

            def _last_done_payload() -> dict:
                for event in reversed(trace_events):
                    if event.get("event") == "done":
                        data = event.get("data")
                        if isinstance(data, dict):
                            return data
                return {}

            def _persist_activity_trace() -> None:
                if not conversation_id:
                    return
                loop_trace = _loop_trace_records()
                first_hash = next(
                    (
                        item.get("system_prompt_hash")
                        for item in loop_trace
                        if item.get("system_prompt_hash")
                    ),
                    None,
                )
                done_payload = _last_done_payload()
                error = persisted_error or done_payload.get("error")
                conv_manager.add_activity_trace(
                    conversation_id,
                    request_id,
                    endpoint="/api/agent_chat_v2",
                    profile=active_profile,
                    provider=provider_name or provider_type,
                    model=model_name,
                    user_message=message,
                    assistant_text="".join(assistant_token_chunks),
                    system_prompt_hash=first_hash,
                    capability_scope=capability_scope,
                    tool_names=sorted(tools),
                    events=trace_events,
                    loop_trace=loop_trace,
                    trace_path=str(trace_path),
                    status="error" if error else "done",
                    total_time_ms=int(done_payload.get("total_time_ms") or 0),
                    error=str(error) if error else None,
                )

            try:
                await emit("activity", {
                    "id": f"{rid}_agent",
                    "type": "agent_start",
                    "title": "Agent v2 started",
                    "detail": f"profile={active_profile} provider={provider_name} model={model_name}",
                    "status": "start",
                    "ts": perf_counter() * 1000,
                    "meta": {
                        "endpoint": "agent_chat_v2",
                        "session_id": request_id,
                        "conversation_id": conversation_id,
                        "profile": active_profile,
                        "provider": provider_name,
                        "model": model_name,
                        "capability_scope": capability_scope,
                        "runtime": runtime_cfg.to_metadata(),
                    },
                })
                await emit("activity", {
                    "id": f"{rid}_tools",
                    "type": "tool_manifest",
                    "title": f"Tools loaded: {len(tools)}",
                    "detail": ", ".join(sorted(tools)),
                    "status": "done",
                    "ts": perf_counter() * 1000,
                    "meta": {
                        "endpoint": "/api/agent_chat_v2",
                        "executor": "AgentLoop",
                        "tools": sorted(tools),
                        "images": len(image_blocks),
                    },
                })
                if image_blocks:
                    await emit("activity", {
                        "id": f"{rid}_images",
                        "type": "input_images",
                        "title": f"Images attached: {len(image_blocks)}",
                        "detail": ", ".join(
                            b.name or b.media_type for b in image_blocks
                        ),
                        "status": "done",
                        "ts": perf_counter() * 1000,
                        "meta": {"count": len(image_blocks)},
                    })
                streamed_text_since_message = False
                async for event in loop.run(message, history=prior, images=image_blocks):
                    if isinstance(event, TextDelta):
                        streamed_text_since_message = True
                        if event.text:
                            await emit("token", {"text": event.text})
                        continue
                    if isinstance(event, ReasoningDelta):
                        if event.text:
                            await emit("activity", {
                                "id": f"{rid}_reasoning",
                                "type": "thinking_update",
                                "title": "Thinking",
                                "detail": event.text,
                                "status": "progress",
                                "ts": perf_counter() * 1000,
                                "meta": {"endpoint": "/api/agent_chat_v2"},
                            })
                        continue
                    if not isinstance(event, Message):
                        continue
                    if event.role == Role.ASSISTANT:
                        for b in event.content:
                            if isinstance(b, TextBlock) and b.text:
                                if not streamed_text_since_message:
                                    await emit("token", {"text": b.text})
                            elif isinstance(b, ToolUseBlock):
                                await emit("activity", {
                                    "id": f"{rid}_tc_{b.id}",
                                    "type": "tool_call",
                                    "title": f"Calling: {b.name}",
                                    "detail": _summarize_tool_input(b.name, b.input),
                                    "status": "start",
                                    "ts": perf_counter() * 1000,
                                    "meta": {
                                        "id": b.id,
                                        "name": b.name,
                                        "input": b.input,
                                    },
                                })
                        streamed_text_since_message = False
                    else:  # Role.USER with tool_result blocks
                        image_feedback = [
                            b for b in event.content if isinstance(b, ImageBlock)
                        ]
                        for b in event.content:
                            if not isinstance(b, ToolResultBlock):
                                continue
                            preview = _summarize_tool_result(b.content)
                            await emit("activity", {
                                "id": f"{rid}_tr_{b.tool_use_id}",
                                "type": "tool_result",
                                "title": "Tool result",
                                "detail": preview,
                                "status": "error" if b.is_error else "done",
                                "ts": perf_counter() * 1000,
                                "meta": {
                                    "is_error": b.is_error,
                                    "tool_use_id": b.tool_use_id,
                                },
                            })
                        if image_feedback:
                            await emit("activity", {
                                "id": f"{rid}_image_feedback_{len(trace_events)}",
                                "type": "image_feedback",
                                "title": f"Image feedback attached: {len(image_feedback)}",
                                "detail": ", ".join(
                                    b.name or b.media_type for b in image_feedback
                                ),
                                "status": "done",
                                "ts": perf_counter() * 1000,
                                "meta": {
                                    "count": len(image_feedback),
                                    "images": [
                                        {
                                            "name": b.name,
                                            "media_type": b.media_type,
                                        }
                                        for b in image_feedback
                                    ],
                                },
                            })

                await emit("done", {
                    "total_time_ms": int((perf_counter() - t0) * 1000),
                    "sources": [],
                    "provider": provider_name or provider_type,
                    "model": model_name,
                })
            except Exception as exc:
                persisted_error = str(exc)
                await emit("activity", {
                    "id": f"{rid}_error",
                    "type": "error",
                    "title": "Agent v2 error",
                    "detail": str(exc),
                    "status": "error",
                    "ts": perf_counter() * 1000,
                    "meta": {},
                })
                await emit("done", {
                    "total_time_ms": int((perf_counter() - t0) * 1000),
                    "error": str(exc),
                    "sources": [],
                    "provider": provider_name or provider_type,
                    "model": model_name,
                })
            finally:
                _persist_activity_trace()
                await stream_queue.put(None)

        async def stream():
            producer = asyncio.create_task(produce())
            try:
                while True:
                    item = await stream_queue.get()
                    if item is None:
                        break
                    event_name, data = item
                    yield fmt(event_name, data)
            finally:
                if not producer.done():
                    producer.cancel()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ========== 对话管理API ==========

    @app.get("/api/conversations")
    async def list_conversations():
        """列出所有对话"""
        return {"ok": True, "conversations": conv_manager.list_all()}

    @app.get("/api/conversations/{conv_id}")
    async def get_conversation(conv_id: str):
        """获取单个对话"""
        conv = conv_manager.get(conv_id)
        if conv:
            return {"ok": True, "conversation": conv}
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    @app.get("/api/conversations/{conv_id}/activity_traces")
    async def list_conversation_activity_traces(conv_id: str):
        """List persisted AgentLoop activity traces for a conversation."""
        if not conv_manager.get(conv_id):
            return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
        return {
            "ok": True,
            "conversation_id": conv_id,
            "traces": conv_manager.list_activity_traces(conv_id),
        }

    @app.get("/api/conversations/{conv_id}/activity_traces/{request_id}")
    async def get_conversation_activity_trace(conv_id: str, request_id: str):
        """Fetch one persisted AgentLoop activity trace."""
        if not conv_manager.get(conv_id):
            return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
        trace = conv_manager.get_activity_trace(conv_id, request_id)
        if not trace:
            return JSONResponse({"ok": False, "error": "Trace not found"}, status_code=404)
        return {"ok": True, "conversation_id": conv_id, "trace": trace}

    @app.get("/api/conversations/{conv_id}/activity_traces/{request_id}/export")
    async def export_conversation_activity_trace(conv_id: str, request_id: str):
        """Export one persisted trace as JSONL for offline debugging."""
        if not conv_manager.get(conv_id):
            return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)
        trace = conv_manager.get_activity_trace(conv_id, request_id)
        if not trace:
            return JSONResponse({"ok": False, "error": "Trace not found"}, status_code=404)

        def lines():
            header = {
                "event": "trace_metadata",
                "data": {
                    key: trace.get(key)
                    for key in (
                        "conversation_id",
                        "request_id",
                        "endpoint",
                        "profile",
                        "provider",
                        "model",
                        "capability_scope",
                        "tool_names",
                        "status",
                        "total_time_ms",
                        "error",
                        "system_prompt_hash",
                    )
                },
            }
            yield json.dumps(header, ensure_ascii=False) + "\n"
            for item in trace.get("events") or []:
                yield json.dumps(item, ensure_ascii=False) + "\n"
            for item in trace.get("loop_trace") or []:
                yield json.dumps(
                    {"event": "loop_trace", "data": item},
                    ensure_ascii=False,
                ) + "\n"

        return StreamingResponse(
            lines(),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{conv_id}_{request_id}_activity.jsonl"'
                )
            },
        )

    @app.post("/api/conversations")
    async def create_conversation(request: Request):
        """创建新对话"""
        payload = await request.json()
        app_cfg = load_app_config(config_dir)
        profile = payload.get("profile") or app_cfg.get("active_profile")
        conv_id = conv_manager.create(profile)
        return {"ok": True, "id": conv_id, "conversation_id": conv_id}

    @app.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str):
        """删除对话"""
        if conv_manager.delete(conv_id):
            return {"ok": True}
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    @app.post("/api/conversations/{conv_id}/messages")
    async def add_conversation_message(conv_id: str, request: Request):
        """添加消息到对话"""
        payload = await request.json()
        role = payload.get("role", "user")
        content = payload.get("content", "")
        model = payload.get("model")
        sources = payload.get("sources")
        if conv_manager.add_message(conv_id, role, content, model, sources):
            return {"ok": True}
        return JSONResponse({"ok": False, "error": "Conversation not found"}, status_code=404)

    # ========== Memory Management API (P2-6) ==========

    @app.get("/api/memories")
    async def list_memories():
        """列出所有记忆"""
        raw_facts = memory_manager.get_facts(limit=100)
        # 转换为前端期望的格式
        memories = []
        for f in raw_facts:
            memories.append({
                "id": f["id"],
                "content": f["fact"],
                "category": f.get("category", "general"),
                "source": f.get("source"),
                "created_at": f.get("created_at")
            })
        # 估算 token 数（约2.5字符/token）
        total_chars = sum(len(m.get("content", "")) for m in memories)
        estimated_tokens = int(total_chars / 2.5)
        return {
            "ok": True,
            "memories": memories,
            "estimated_tokens": estimated_tokens
        }

    @app.delete("/api/memories/{memory_id}")
    async def delete_memory(memory_id: str):
        """删除单个记忆"""
        try:
            fact_id = int(memory_id)
            if memory_manager.delete_fact(fact_id):
                return {"ok": True}
            return JSONResponse({"ok": False, "error": "Memory not found"}, status_code=404)
        except ValueError:
            return JSONResponse({"ok": False, "error": "Invalid memory ID"}, status_code=400)

    return app


def run(host: str = "127.0.0.1", port: int = 8686, config_dir: str | None = None) -> None:
    import uvicorn

    app = create_app(config_dir)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
