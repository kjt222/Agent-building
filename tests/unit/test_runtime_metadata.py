from __future__ import annotations

import json

from agent.core.runtime import (
    RuntimeConfig,
    SessionMetadata,
    build_agent_system_prompt,
)


def test_runtime_config_defaults_without_app_section():
    cfg = RuntimeConfig.from_app_config({})

    assert cfg.mode == "inline"
    assert cfg.monitor_enabled is False
    assert cfg.wake_on_task_complete is False
    assert cfg.heartbeat_seconds == 30


def test_runtime_config_reads_monitor_section():
    cfg = RuntimeConfig.from_app_config({
        "runtime": {
            "mode": "monitor",
            "monitor": {
                "enabled": True,
                "wake_on_task_complete": True,
                "heartbeat_seconds": 10,
            },
        },
    })

    assert cfg.mode == "monitor"
    assert cfg.monitor_enabled is True
    assert cfg.wake_on_task_complete is True
    assert cfg.heartbeat_seconds == 10


def test_session_metadata_json_is_model_readable():
    metadata = SessionMetadata(
        session_id="abc123",
        conversation_id="conv1",
        endpoint="/api/agent_chat_v2",
        executor="AgentLoop",
        profile="gpt-5.4",
        provider="openai",
        provider_type="openai",
        model="gpt-5.4",
        active_kbs=("kb-a",),
        tool_names=("Bash", "Read"),
        cwd="D:/repo",
        runtime={"mode": "inline", "monitor_enabled": False},
    )

    data = json.loads(metadata.to_json())

    assert data["model"] == "gpt-5.4"
    assert data["provider"] == "openai"
    assert data["tool_names"] == ["Bash", "Read"]
    assert data["active_kbs"] == ["kb-a"]


def test_system_prompt_injects_authoritative_metadata():
    metadata = SessionMetadata(
        session_id="abc123",
        conversation_id=None,
        endpoint="/api/agent_chat_v2",
        executor="AgentLoop",
        profile="gpt-5.4",
        provider="openai",
        provider_type="openai",
        model="gpt-5.4",
    )

    prompt = build_agent_system_prompt("base instruction", metadata)

    assert "base instruction" in prompt
    assert "<session_metadata>" in prompt
    assert '"model": "gpt-5.4"' in prompt
    assert "你是什么模型" in prompt
    assert "Do not call a tool just to inspect runtime metadata" in prompt
