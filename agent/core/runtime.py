"""Runtime metadata injected into AgentLoop system context.

This is intentionally context, not a tool. The model should answer questions
about its current session/model/profile from the injected metadata instead of
spending a tool call on runtime introspection.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class RuntimeConfig:
    """Config surface for the request runtime and future monitor process."""

    mode: str = "inline"
    monitor_enabled: bool = False
    wake_on_task_complete: bool = False
    heartbeat_seconds: int = 30

    @classmethod
    def from_app_config(cls, app_cfg: dict) -> "RuntimeConfig":
        raw = app_cfg.get("runtime") or {}
        if not isinstance(raw, dict):
            raw = {}
        monitor = raw.get("monitor") or {}
        if not isinstance(monitor, dict):
            monitor = {}
        return cls(
            mode=str(raw.get("mode") or "inline"),
            monitor_enabled=bool(monitor.get("enabled", False)),
            wake_on_task_complete=bool(monitor.get("wake_on_task_complete", False)),
            heartbeat_seconds=int(monitor.get("heartbeat_seconds") or 30),
        )

    def to_metadata(self) -> dict:
        return {
            "mode": self.mode,
            "monitor_enabled": self.monitor_enabled,
            "wake_on_task_complete": self.wake_on_task_complete,
            "heartbeat_seconds": self.heartbeat_seconds,
        }


@dataclass(frozen=True)
class SessionMetadata:
    session_id: str
    conversation_id: str | None
    endpoint: str
    executor: str
    profile: str
    provider: str
    provider_type: str
    model: str
    active_kbs: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    cwd: str = ""
    runtime: dict = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_prompt_dict(self) -> dict:
        data = asdict(self)
        data["active_kbs"] = list(self.active_kbs)
        data["tool_names"] = list(self.tool_names)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_prompt_dict(), ensure_ascii=False, indent=2)


def build_agent_system_prompt(base_prompt: str, metadata: SessionMetadata) -> str:
    """Append authoritative session metadata to the system prompt."""

    return (
        f"{base_prompt.strip()}\n\n"
        "Runtime/session metadata is authoritative for this request. "
        "When the user asks what model, provider, profile, endpoint, tools, "
        "knowledge bases, working directory, or runtime mode you are using, "
        "answer from this metadata directly. Do not call a tool just to inspect "
        "runtime metadata. For questions such as '你是什么模型', '当前模型', "
        "'what model are you', or 'which provider are you using', answer in "
        "one concise sentence using metadata.model, metadata.provider, and "
        "metadata.profile; do not introduce general capabilities or ask for "
        "clarification.\n\n"
        "<session_metadata>\n"
        f"{metadata.to_json()}\n"
        "</session_metadata>"
    )
