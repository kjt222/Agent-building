"""Tests for the P3 Verify browser tool."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.core.loop import LoopConfig, LoopContext, PermissionLevel
from agent.tools_v2.primitives import full_toolset
from agent.tools_v2.verify_tool import VerifyTool


pytest.importorskip("playwright.sync_api")


def _ctx() -> LoopContext:
    return LoopContext(config=LoopConfig())


def _result_json(result):
    assert result.is_error is False
    return json.loads(result.content)


def test_verify_tool_passes_basic_html_assertions(tmp_path):
    target = tmp_path / "ok.html"
    target.write_text(
        "<!doctype html><html><body><h1>Hello</h1><canvas></canvas></body></html>",
        encoding="utf-8",
    )

    result = asyncio.run(
        VerifyTool().run(
            {
                "target": str(target),
                "assertions": [
                    {"type": "selector_exists", "selector": "canvas"},
                    {"type": "text_contains", "selector": "body", "text": "Hello"},
                    {"type": "no_console_errors"},
                ],
                "screenshot_path": str(tmp_path / "ok.png"),
            },
            _ctx(),
        )
    )

    payload = _result_json(result)
    assert payload["ok"] is True
    assert payload["console_errors"] == []
    assert (tmp_path / "ok.png").exists()


def test_verify_tool_reports_failed_style_assertion(tmp_path):
    target = tmp_path / "bad_snake.html"
    target.write_text(
        """
        <!doctype html>
        <html><body>
          <div id="game-over" style="display:block">Game Over!</div>
        </body></html>
        """,
        encoding="utf-8",
    )

    result = asyncio.run(
        VerifyTool().run(
            {
                "target": str(target),
                "assertions": [
                    {
                        "type": "style_equals",
                        "selector": "#game-over",
                        "property": "display",
                        "value": "none",
                    }
                ],
                "screenshot_path": str(tmp_path / "bad.png"),
            },
            _ctx(),
        )
    )

    payload = _result_json(result)
    assert payload["ok"] is False
    [assertion] = payload["assertions"]
    assert assertion["ok"] is False
    assert assertion["actual"] == "block"
    assert (tmp_path / "bad.png").exists()


def test_full_toolset_exposes_verify_as_safe_serial_tool():
    tool = full_toolset()["Verify"]
    assert tool.permission_level == PermissionLevel.SAFE
    assert tool.parallel_safe is False
