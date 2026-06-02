"""HTTP-level smoke for /api/files/search + @file injection (P12.5)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.core.loop import TextDelta, TurnEnd
from agent.ui.server import create_app


def _write_config(tmp_path: Path) -> None:
    (tmp_path / "app.yaml").write_text(
        """
active_profile: test
profiles:
  test: {}
active_kbs: []
knowledge_bases: []
runtime:
  mode: inline
  monitor:
    enabled: false
    wake_on_task_complete: false
    heartbeat_seconds: 30
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "models.yaml").write_text(
        """
profiles:
  test:
    llm:
      active: provider
      providers:
        provider:
          type: openai
          model: gpt-5.4
          api_key_ref: test.llm.provider
""".strip(),
        encoding="utf-8",
    )


class _CapturingAdapter:
    """Adapter that records the system prompt it was handed, then exits."""

    last_system: str | None = None

    def __init__(self, model, api_key, base_url=None):
        self.model = model

    async def stream(self, messages, tools, system=None, **options):
        type(self).last_system = system or ""
        yield TextDelta(text="done.")
        yield TurnEnd(
            stop_reason="end_turn",
            usage={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        )


@pytest.fixture
def workspace_client(tmp_path, monkeypatch):
    """A TestClient where Path.cwd() resolves to a temp workspace with files."""

    # Build the workspace under a sub-dir so config files live separately.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "agent" / "core").mkdir(parents=True)
    (workspace / "agent" / "core" / "loop.py").write_text("# loop", encoding="utf-8")
    (workspace / "agent" / "core" / "hooks.py").write_text("# hooks", encoding="utf-8")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "implementation.md").write_text("# impl", encoding="utf-8")
    (workspace / ".venv").mkdir()
    (workspace / ".venv" / "loop.py").write_text("noise", encoding="utf-8")

    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    _write_config(cfg_dir)

    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    monkeypatch.setattr(
        "agent.models.openai_responses_adapter.OpenAIResponsesAdapter",
        _CapturingAdapter,
    )

    original_cwd = os.getcwd()
    os.chdir(workspace)
    try:
        client = TestClient(create_app(str(cfg_dir)))
        _CapturingAdapter.last_system = None
        yield client, workspace
    finally:
        os.chdir(original_cwd)


def test_search_endpoint_returns_matches(workspace_client):
    client, _ws = workspace_client
    res = client.get("/api/files/search", params={"q": "loop"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    paths = [item["path"] for item in body["items"]]
    assert "agent/core/loop.py" in paths
    # .venv must NOT appear in results.
    assert all(".venv" not in p for p in paths), paths


def test_search_endpoint_empty_query_returns_some_items(workspace_client):
    client, _ws = workspace_client
    res = client.get("/api/files/search", params={"q": ""})
    body = res.json()
    assert body["ok"] is True
    assert len(body["items"]) > 0


def test_search_endpoint_honors_limit(workspace_client):
    client, ws = workspace_client
    for i in range(15):
        (ws / f"extra_{i}.py").write_text("x", encoding="utf-8")
    res = client.get("/api/files/search", params={"q": "extra", "limit": 3})
    assert len(res.json()["items"]) == 3


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        event, data = "", ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event and data:
            try:
                out.append((event, json.loads(data)))
            except json.JSONDecodeError:
                out.append((event, {"raw": data}))
    return out


def test_attached_files_injected_into_system_prompt(workspace_client):
    """End-to-end: a @path token in the user message lands in the prompt."""
    client, ws = workspace_client
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Look at @agent/core/loop.py please.",
            "conversation_id": "conv-attached-1",
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    sys_prompt = _CapturingAdapter.last_system or ""
    assert "<attached_files>" in sys_prompt, sys_prompt[:400]
    # The absolute path must be present (we inject absolute paths so the
    # model does not have to guess where root is).
    expected_abs = str((ws / "agent" / "core" / "loop.py").resolve())
    assert expected_abs in sys_prompt, sys_prompt[:400]


def test_attached_files_block_absent_when_no_mention(workspace_client):
    client, _ws = workspace_client
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Just a plain question without any mention.",
            "conversation_id": "conv-attached-2",
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    sys_prompt = _CapturingAdapter.last_system or ""
    assert "<attached_files>" not in sys_prompt


def test_attached_files_ignores_unknown_paths(workspace_client):
    client, _ws = workspace_client
    res = client.post(
        "/api/agent_chat_v2",
        json={
            "message": "Compare @agent/core/loop.py with @does/not/exist.py please.",
            "conversation_id": "conv-attached-3",
            "max_iterations": 1,
        },
    )
    assert res.status_code == 200
    sys_prompt = _CapturingAdapter.last_system or ""
    # Isolate the attached_files block so the assertion is not fooled by
    # the user message getting echoed elsewhere in the prompt.
    start = sys_prompt.find("<attached_files>")
    end = sys_prompt.find("</attached_files>")
    assert start >= 0 and end > start, sys_prompt[:400]
    block = sys_prompt[start:end]
    assert "loop.py" in block
    # The made-up path must NOT appear inside the attached block.
    assert "does/not/exist.py" not in block
