"""DB + API tests for the conversation fork feature (P12.7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent.storage.conversation_adapter import ConversationManager
from agent.storage.database import Database
from agent.ui.server import create_app


# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path) -> ConversationManager:
    db = Database(tmp_path / "agent.db")
    return ConversationManager(tmp_path, db=db)


def _seed(manager: ConversationManager) -> tuple[str, list[dict]]:
    """Create a conversation with user/asst/user/asst, return (id, messages)."""
    conv_id = manager.create("test-profile")
    manager.add_message(conv_id, "user", "first question?")
    manager.add_message(conv_id, "assistant", "first answer.")
    manager.add_message(conv_id, "user", "second question?")
    manager.add_message(conv_id, "assistant", "second answer.")
    conv = manager.get(conv_id)
    return conv_id, conv["messages"]


def test_get_conversation_now_returns_message_ids(manager):
    conv_id, msgs = _seed(manager)
    assert len(msgs) == 4
    assert all("id" in m for m in msgs)
    assert all(isinstance(m["id"], int) for m in msgs)


def test_fork_from_first_user_message_copies_nothing(manager):
    conv_id, msgs = _seed(manager)
    result = manager.fork(conv_id, msgs[0]["id"])
    assert result is not None
    assert result["copied_message_count"] == 0
    new_conv = manager.get(result["new_conversation_id"])
    assert new_conv is not None
    assert new_conv["messages"] == []


def test_fork_from_second_user_message_copies_first_two(manager):
    conv_id, msgs = _seed(manager)
    # msgs index 2 is the second user message ("second question?").
    result = manager.fork(conv_id, msgs[2]["id"])
    assert result is not None
    assert result["copied_message_count"] == 2
    new_conv = manager.get(result["new_conversation_id"])
    contents = [m["content"] for m in new_conv["messages"]]
    assert contents == ["first question?", "first answer."]
    # Original conversation is untouched.
    original = manager.get(conv_id)
    assert len(original["messages"]) == 4


def test_fork_rejects_assistant_message(manager):
    conv_id, msgs = _seed(manager)
    # msgs index 1 is an assistant message.
    result = manager.fork(conv_id, msgs[1]["id"])
    assert result is None


def test_fork_rejects_unknown_message_id(manager):
    conv_id, _ = _seed(manager)
    result = manager.fork(conv_id, 99_999)
    assert result is None


def test_fork_rejects_cross_conversation_message_id(manager):
    conv_a, msgs_a = _seed(manager)
    # Create a second conversation and try to fork conv_a using a message
    # that belongs to it instead.
    conv_b = manager.create("other")
    manager.add_message(conv_b, "user", "from-b")
    conv_b_msg = manager.get(conv_b)["messages"][0]
    result = manager.fork(conv_a, conv_b_msg["id"])
    assert result is None


def test_fork_new_conversation_has_fork_suffix(manager):
    conv_id, msgs = _seed(manager)
    # Title defaults to "New Conversation" so check suffix specifically.
    result = manager.fork(conv_id, msgs[2]["id"])
    new_conv = manager.get(result["new_conversation_id"])
    assert "（分叉）" in (new_conv.get("title") or "")


def test_fork_new_conversation_inherits_profile(manager):
    conv_id = manager.create("custom-profile-7")
    manager.add_message(conv_id, "user", "u1")
    manager.add_message(conv_id, "assistant", "a1")
    manager.add_message(conv_id, "user", "u2")
    user2_id = manager.get(conv_id)["messages"][2]["id"]
    result = manager.fork(conv_id, user2_id)
    new_conv = manager.get(result["new_conversation_id"])
    assert new_conv.get("profile") == "custom-profile-7"


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------


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
def client(tmp_path, monkeypatch) -> TestClient:
    _write_config(tmp_path)
    monkeypatch.setattr("agent.ui.server.resolve_api_key", lambda **_: "test-key")
    return TestClient(create_app(str(tmp_path)))


def _seed_via_api(client: TestClient) -> tuple[str, list[dict]]:
    res = client.post("/api/conversations", json={})
    conv_id = res.json()["conversation_id"]
    for role, text in [
        ("user", "first question?"),
        ("assistant", "first answer."),
        ("user", "second question?"),
        ("assistant", "second answer."),
    ]:
        client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"role": role, "content": text},
        )
    return conv_id, client.get(f"/api/conversations/{conv_id}").json()["conversation"]["messages"]


def test_endpoint_forks_from_user_message(client):
    conv_id, msgs = _seed_via_api(client)
    res = client.post(
        f"/api/conversations/{conv_id}/fork",
        json={"from_message_id": msgs[2]["id"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["copied_message_count"] == 2
    new_id = body["new_conversation_id"]
    # The new conversation should be addressable and have exactly two msgs.
    new = client.get(f"/api/conversations/{new_id}").json()["conversation"]
    contents = [m["content"] for m in new["messages"]]
    assert contents == ["first question?", "first answer."]
    # Original is untouched.
    original = client.get(f"/api/conversations/{conv_id}").json()["conversation"]
    assert len(original["messages"]) == 4


def test_endpoint_rejects_assistant_message(client):
    conv_id, msgs = _seed_via_api(client)
    res = client.post(
        f"/api/conversations/{conv_id}/fork",
        json={"from_message_id": msgs[1]["id"]},
    )
    assert res.status_code == 400


def test_endpoint_rejects_missing_payload(client):
    conv_id, _ = _seed_via_api(client)
    res = client.post(f"/api/conversations/{conv_id}/fork", json={})
    assert res.status_code == 400


def test_endpoint_rejects_unknown_conversation(client):
    res = client.post(
        "/api/conversations/no-such-id/fork",
        json={"from_message_id": 1},
    )
    assert res.status_code == 404


def test_endpoint_rejects_non_integer_message_id(client):
    conv_id, _ = _seed_via_api(client)
    res = client.post(
        f"/api/conversations/{conv_id}/fork",
        json={"from_message_id": "not-an-int"},
    )
    assert res.status_code == 400
