"""Production boot smoke: the app must construct, serve its runtime probe, and
keep every restored P12 route registered.

Cheap regression gate (no live model): if create_app() raises on import/wiring,
or a recovered endpoint gets dropped again, this fails fast instead of waiting
for the heavier feature tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.ui.server import create_app


def _write_config(tmp_path):
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    _write_config(tmp_path)
    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    return TestClient(create_app(str(tmp_path)))


def test_app_boots_and_runtime_probe_responds(client):
    res = client.get("/api/agent_runtime")
    assert res.status_code == 200
    body = res.json()
    assert body["chat_endpoint"] == "/api/agent_chat_v2"
    assert body["chat_executor"] == "AgentLoop"


# Every route restored during the P1 web-layer recovery. If any of these
# disappears again, this list catches it before a feature test does.
_REQUIRED_ROUTES = [
    ("/api/conversations/{conv_id}/fork", "POST"),
    ("/api/files/search", "GET"),
    ("/api/conversations/{conv_id}/usage", "GET"),
    ("/api/user_questions/{question_id}", "POST"),
    ("/api/diff_previews/{preview_id}", "POST"),
    ("/api/conversations/{conv_id}/interrupt", "POST"),
    ("/api/agent_chat_v2", "POST"),
    ("/api/conversations/{conv_id}/activity_traces", "GET"),
]


def test_all_restored_routes_registered(client):
    registered = {
        (route.path, method)
        for route in client.app.routes
        for method in getattr(route, "methods", []) or []
    }
    missing = [
        (path, method)
        for path, method in _REQUIRED_ROUTES
        if (path, method) not in registered
    ]
    assert not missing, f"routes dropped: {missing}"


def test_unknown_route_is_404(client):
    assert client.get("/api/definitely_not_a_route").status_code == 404
