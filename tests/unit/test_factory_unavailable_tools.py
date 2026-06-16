"""Factory must degrade gracefully for tools whose module was lost in the
D-drive-format recovery (web_tool / image_tool), instead of crashing the whole
tool-build with ModuleNotFoundError."""

from __future__ import annotations

import pytest

from agent.tools_v2.factory import build_tool, build_tools, _UnavailableTool


@pytest.mark.parametrize("name", ["WebSearch", "FetchURL", "Image"])
def test_missing_module_tools_build_without_raising(name):
    # Must not raise even though the backing module is absent; returns either
    # the real tool (if someone reinstalls it) or the unavailable stub.
    tool = build_tool(name, {})
    assert tool.name == name
    assert hasattr(tool, "run")


def test_build_tools_does_not_crash_on_mixed_set():
    out = build_tools(["Read", "WebSearch", "Image"], {})
    assert set(out) == {"Read", "WebSearch", "Image"}


@pytest.mark.asyncio
async def test_unavailable_tool_returns_actionable_error():
    tool = _UnavailableTool("WebSearch", "the web_tool module is not installed")
    result = await tool.run({}, None)
    assert result.is_error is True
    assert "unavailable" in result.content.lower()
    assert "WebSearch" in result.content


def test_unknown_tool_still_raises_keyerror():
    with pytest.raises(KeyError):
        build_tool("NoSuchToolXYZ", {})
