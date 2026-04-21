"""Contract tests for Claude-Code-style primitive file tools."""

from __future__ import annotations

import asyncio
import sys

from agent.core.loop import LoopConfig, LoopContext, PermissionLevel
from agent.tools_v2.primitives import (
    BashTool,
    EditTool,
    ReadTool,
    WriteTool,
    default_toolset,
)


def _ctx() -> LoopContext:
    return LoopContext(config=LoopConfig())


def test_write_existing_file_requires_read_first(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    ctx = _ctx()

    result = asyncio.run(
        WriteTool().run({"path": str(target), "content": "new"}, ctx)
    )

    assert result.is_error is True
    assert "Read it first" in result.content
    assert target.read_text(encoding="utf-8") == "old"


def test_edit_requires_read_first(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("old", encoding="utf-8")
    ctx = _ctx()

    result = asyncio.run(
        EditTool().run(
            {"path": str(target), "old_string": "old", "new_string": "new"},
            ctx,
        )
    )

    assert result.is_error is True
    assert "Read it first" in result.content
    assert target.read_text(encoding="utf-8") == "old"


def test_read_then_edit_succeeds_with_exact_unique_string(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    ctx = _ctx()

    read_result = asyncio.run(ReadTool().run({"path": str(target)}, ctx))
    edit_result = asyncio.run(
        EditTool().run(
            {"path": str(target), "old_string": "beta", "new_string": "gamma"},
            ctx,
        )
    )

    assert read_result.is_error is False
    assert edit_result.is_error is False
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"


def test_edit_rejects_ambiguous_old_string_without_replace_all(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("same\nsame\n", encoding="utf-8")
    ctx = _ctx()
    asyncio.run(ReadTool().run({"path": str(target)}, ctx))

    result = asyncio.run(
        EditTool().run(
            {"path": str(target), "old_string": "same", "new_string": "new"},
            ctx,
        )
    )

    assert result.is_error is True
    assert "appears 2 times" in result.content
    assert target.read_text(encoding="utf-8") == "same\nsame\n"


def test_primitive_tool_protocol_flags_are_aligned():
    tools = default_toolset()
    assert set(tools) == {"Bash", "Read", "Write", "Edit", "Glob", "Grep"}

    assert tools["Read"].permission_level == PermissionLevel.SAFE
    assert tools["Glob"].permission_level == PermissionLevel.SAFE
    assert tools["Grep"].permission_level == PermissionLevel.SAFE
    assert tools["Read"].parallel_safe is True
    assert tools["Glob"].parallel_safe is True
    assert tools["Grep"].parallel_safe is True

    assert tools["Bash"].permission_level == PermissionLevel.NEEDS_APPROVAL
    assert tools["Write"].permission_level == PermissionLevel.NEEDS_APPROVAL
    assert tools["Edit"].permission_level == PermissionLevel.NEEDS_APPROVAL
    assert tools["Bash"].parallel_safe is False
    assert tools["Write"].parallel_safe is False
    assert tools["Edit"].parallel_safe is False

    for tool in tools.values():
        assert tool.name
        assert tool.description
        assert tool.input_schema["type"] == "object"


def test_bash_allows_simple_command_and_returns_structured_result():
    result = asyncio.run(
        BashTool().run(
            {"command": f'"{sys.executable}" -c "print(42)"'},
            _ctx(),
        )
    )

    assert result.is_error is False
    assert "[stdout]\n42" in result.content
    assert "[stderr]\n" in result.content
    assert "[exit_code]\n0" in result.content


def test_bash_blocks_unallowlisted_command():
    result = asyncio.run(BashTool().run({"command": "curl https://example.com"}, _ctx()))

    assert result.is_error is True
    assert "not in the Bash allowlist" in result.content


def test_bash_blocks_shell_control_and_dangerous_commands():
    shell_control = asyncio.run(
        BashTool().run({"command": f'"{sys.executable}" -c "print(1)" && git status'}, _ctx())
    )
    dangerous = asyncio.run(BashTool().run({"command": "Remove-Item x"}, _ctx()))

    assert shell_control.is_error is True
    assert "shell control operator" in shell_control.content
    assert dangerous.is_error is True
    assert "dangerous command" in dangerous.content


def test_bash_restricts_git_to_read_only_subcommands():
    allowed = asyncio.run(BashTool().run({"command": "git status --short"}, _ctx()))
    blocked = asyncio.run(BashTool().run({"command": "git push"}, _ctx()))

    assert allowed.is_error is False
    assert "[exit_code]\n0" in allowed.content
    assert blocked.is_error is True
    assert "git subcommand" in blocked.content
