"""HTTP-level smoke for diff_preview endpoint (P12.2).

End-to-end behaviour (hook → SSE → endpoint → tool execution) is exercised
by the Playwright UI smoke in ``tests/p12_diff_preview_ui/``; here we cover
just the surfaces that don't need a live loop:

- 404 for unknown preview id;
- future resolution shape via the endpoint.
"""

from __future__ import annotations

import asyncio

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
    yield TestClient(create_app(str(tmp_path)))


def test_endpoint_returns_404_for_unknown(client):
    res = client.post("/api/diff_previews/nope", json={"approved": True})
    assert res.status_code == 404


def test_endpoint_resolves_pending_future_with_approval(client):
    pending = client.app.state.pending_diff_previews

    async def _drive():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        pending["pid-1"] = {"future": fut}
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            cf = pool.submit(
                client.post,
                "/api/diff_previews/pid-1",
                json={"approved": True, "note": "looks good"},
            )
            res = await loop.run_in_executor(None, cf.result)
        reply = await asyncio.wait_for(fut, timeout=2)
        return res, reply

    res, reply = asyncio.run(_drive())
    assert res.status_code == 200
    assert reply == {"approved": True, "note": "looks good"}


def test_endpoint_resolves_pending_future_with_rejection(client):
    pending = client.app.state.pending_diff_previews

    async def _drive():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        pending["pid-2"] = {"future": fut}
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            cf = pool.submit(
                client.post,
                "/api/diff_previews/pid-2",
                json={"approved": False},
            )
            await loop.run_in_executor(None, cf.result)
        return await asyncio.wait_for(fut, timeout=2)

    reply = asyncio.run(_drive())
    assert reply["approved"] is False


def test_endpoint_idempotent_when_already_resolved(client):
    pending = client.app.state.pending_diff_previews

    async def _setup():
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        pending["pid-3"] = {"future": fut}
        fut.set_result({"approved": True})

    asyncio.run(_setup())
    res = client.post("/api/diff_previews/pid-3", json={"approved": False})
    assert res.status_code == 200
