from pathlib import Path


def test_frontend_wires_tool_approval_prompter():
    js = Path("agent/ui/static/js/app.js").read_text(encoding="utf-8")

    assert "approval_request" in js
    assert "/api/tool_approvals/" in js
    assert "window.confirm" in js
