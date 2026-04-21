"""Message/tool conversion tests for both OpenAI adapters (chat + responses)."""

from __future__ import annotations

import json

import pytest

from agent.core.loop import (
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agent.models.openai_adapter_v2 import (
    _internal_to_openai,
    _internal_tools_to_openai,
)
from agent.models.openai_responses_adapter import (
    _internal_to_responses_input,
    _internal_tools_to_responses,
)


# --------------------------------------------------------------------------- #
# Chat Completions shape
# --------------------------------------------------------------------------- #


def test_chat_user_text_only():
    msgs = [Message(role=Role.USER, content=[TextBlock(text="hi")])]
    out = _internal_to_openai(msgs, system=None)
    assert out == [{"role": "user", "content": "hi"}]


def test_chat_system_prefix():
    out = _internal_to_openai(
        [Message(role=Role.USER, content=[TextBlock(text="q")])],
        system="be helpful",
    )
    assert out[0] == {"role": "system", "content": "be helpful"}
    assert out[1] == {"role": "user", "content": "q"}


def test_chat_user_with_image_block():
    out = _internal_to_openai(
        [Message(role=Role.USER, content=[
            TextBlock(text="describe"),
            ImageBlock(base64="abc", media_type="image/png", name="a.png"),
        ])],
        system=None,
    )
    assert out == [{
        "role": "user",
        "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ],
    }]


def test_chat_assistant_with_tool_calls():
    msgs = [
        Message(
            role=Role.ASSISTANT,
            content=[
                TextBlock(text="let me check"),
                ToolUseBlock(id="call_x", name="read", input={"path": "a.py"}),
            ],
        )
    ]
    [entry] = _internal_to_openai(msgs, system=None)
    assert entry["role"] == "assistant"
    assert entry["content"] == "let me check"
    assert entry["tool_calls"][0]["id"] == "call_x"
    assert entry["tool_calls"][0]["function"]["name"] == "read"
    assert json.loads(entry["tool_calls"][0]["function"]["arguments"]) == {"path": "a.py"}


def test_chat_tool_result_becomes_role_tool():
    msgs = [
        Message(
            role=Role.USER,
            content=[ToolResultBlock(tool_use_id="call_x", content="file body")],
        )
    ]
    [entry] = _internal_to_openai(msgs, system=None)
    assert entry == {"role": "tool", "tool_call_id": "call_x", "content": "file body"}


def test_chat_tool_result_dict_content_is_json_serialised():
    msgs = [
        Message(
            role=Role.USER,
            content=[ToolResultBlock(tool_use_id="c", content=[{"k": "v"}])],
        )
    ]
    [entry] = _internal_to_openai(msgs, system=None)
    assert entry["content"] == '[{"k": "v"}]'


def test_chat_tools_schema_has_function_wrapper():
    out = _internal_tools_to_openai(
        [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    )
    assert out == [
        {
            "type": "function",
            "function": {"name": "t", "description": "d", "parameters": {"type": "object"}},
        }
    ]


# --------------------------------------------------------------------------- #
# Responses API shape
# --------------------------------------------------------------------------- #


def test_responses_user_text_only():
    out = _internal_to_responses_input(
        [Message(role=Role.USER, content=[TextBlock(text="hi")])]
    )
    assert out == [{"role": "user", "content": "hi"}]


def test_responses_user_with_image_block():
    out = _internal_to_responses_input(
        [Message(role=Role.USER, content=[
            TextBlock(text="describe"),
            ImageBlock(base64="abc", media_type="image/png", name="a.png"),
        ])]
    )
    assert out == [{
        "role": "user",
        "content": [
            {"type": "input_text", "text": "describe"},
            {"type": "input_image", "image_url": "data:image/png;base64,abc"},
        ],
    }]


def test_responses_assistant_tool_call_is_flat_item():
    msgs = [
        Message(
            role=Role.ASSISTANT,
            content=[
                TextBlock(text="plan"),
                ToolUseBlock(id="call_7", name="grep", input={"q": "foo"}),
            ],
        )
    ]
    items = _internal_to_responses_input(msgs)
    assert items[0] == {"role": "assistant", "content": "plan"}
    assert items[1]["type"] == "function_call"
    assert items[1]["call_id"] == "call_7"
    assert items[1]["name"] == "grep"
    assert json.loads(items[1]["arguments"]) == {"q": "foo"}


def test_responses_tool_result_is_function_call_output():
    msgs = [
        Message(
            role=Role.USER,
            content=[ToolResultBlock(tool_use_id="call_7", content="matched 3")],
        )
    ]
    [item] = _internal_to_responses_input(msgs)
    assert item == {
        "type": "function_call_output",
        "call_id": "call_7",
        "output": "matched 3",
    }


def test_responses_tools_schema_is_flat():
    out = _internal_tools_to_responses(
        [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    )
    # No nested `function:` wrapper on Responses API.
    assert out == [
        {"type": "function", "name": "t", "description": "d", "parameters": {"type": "object"}}
    ]
