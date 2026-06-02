"""Regression tests for unattended chat_v2 runs and the profile override.

These assert the source of agent/ui/server.py contains the wiring (same style
as test_base_agent_prompt.py). The chat_v2 handler is a long inline FastAPI
endpoint; lifting it into a testable unit would require a deeper refactor
than the bug fix warrants.
"""

from pathlib import Path


def _server_text() -> str:
    return Path("agent/ui/server.py").read_text(encoding="utf-8")


def test_chat_v2_reads_unattended_flag_from_payload():
    text = _server_text()
    assert 'unattended = bool(payload.get("unattended") or False)' in text


def test_chat_v2_emits_auto_deny_for_unattended_approvals():
    text = _server_text()
    # The auto-deny event must announce why so the model sees it in the
    # tool result feed, not just on the activity log.
    assert "approval_auto_deny" in text
    assert "unattended_no_approver" in text
    assert "Mode is restricted with no approver." in text


def test_chat_v2_unattended_branch_runs_before_human_wait():
    text = _server_text()
    # Order matters: inside approval_prompter the unattended fast-fail must
    # be evaluated before the 300 s asyncio.wait_for that gates the human
    # approval. Scope the comparison to the approval_prompter function body
    # (find the def, then a chunk after it) so unrelated wait_for calls
    # (plan-approval handler etc.) don't poison the indices.
    start = text.find("async def approval_prompter(")
    assert start >= 0, "approval_prompter handler must exist"
    body = text[start : start + 10000]
    idx_unattended = body.find("if unattended:")
    idx_wait = body.find("await asyncio.wait_for(future, timeout=timeout)")
    assert 0 < idx_unattended < idx_wait, (
        f"unattended branch ({idx_unattended}) must precede the human-wait "
        f"call ({idx_wait}) inside approval_prompter."
    )


def test_chat_v2_honours_profile_override_in_payload():
    text = _server_text()
    # The runner needs to switch between doubao-code and gpt-5.5 without
    # editing config/app.yaml between runs.
    assert 'profile_override = str(payload.get("profile") or "").strip()' in text
    assert "profile_override or str(app_cfg.get(\"active_profile\")" in text
