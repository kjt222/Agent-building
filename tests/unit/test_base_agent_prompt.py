"""Regression: base_agent_prompt in agent/ui/server.py must include the
"locate named entities by name first, not by mtime" rule (#101).

The prompt is built inline inside `chat_v2`, so we assert against the source
file directly instead of importing.
"""

from pathlib import Path


def _server_text() -> str:
    return Path("agent/ui/server.py").read_text(encoding="utf-8")


def test_base_agent_prompt_mentions_named_entity_search():
    text = _server_text()
    # Key fragments from the #101 system-prompt rule.
    assert "names a specific entity" in text
    assert "Glob" in text
    assert "Grep" in text


def test_base_agent_prompt_forbids_picking_by_recency():
    text = _server_text()
    # Catches a regression where the recency caveat is removed. The
    # prompt is concatenated across several Python string literals, so we
    # match substrings that stay inside one literal.
    assert "pick a candidate by recency" in text
    assert "mtime" in text
    assert "listdir order" in text


def test_base_agent_prompt_requires_ask_before_guessing_on_zero_match():
    text = _server_text()
    assert "name-based search returns zero" in text
    assert "ask the user before guessing" in text
