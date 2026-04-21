"""Scripted ModelAdapter for unit-testing AgentLoop without API calls.

Each "script" is a list of Deltas for one turn. The adapter yields them in
order; run() drives the loop through as many turns as there are scripts.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

from agent.core.loop import (
    Delta,
    Message,
    ReasoningDelta,
    TextDelta,
    ToolUseDelta,
    TurnEnd,
)


class MockAdapter:
    """ModelAdapter that emits scripted deltas — one list per turn."""

    name = "mock"

    def __init__(self, scripts: list[list[Delta]]):
        self._scripts = list(scripts)
        self._call_log: list[dict] = []

    @property
    def call_log(self) -> list[dict]:
        """Records each stream() call's (messages, tools, system) snapshot."""
        return self._call_log

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict],
        system: Optional[str] = None,
        **options,
    ) -> AsyncIterator[Delta]:
        self._call_log.append(
            {
                "messages": list(messages),
                "tool_names": [t["name"] for t in (tools or [])],
                "system": system,
                "options": dict(options),
            }
        )
        if not self._scripts:
            # Default: a clean end_turn so over-driving doesn't hang.
            yield TurnEnd(stop_reason="end_turn")
            return
        script = self._scripts.pop(0)
        for d in script:
            yield d


def text_turn(text: str, *, usage: Optional[dict] = None) -> list[Delta]:
    """Single-turn script: assistant replies with text and ends."""
    return [TextDelta(text=text), TurnEnd(stop_reason="end_turn", usage=usage or {})]


def tool_turn(
    tool_id: str,
    tool_name: str,
    tool_input: dict,
    *,
    leading_text: str = "",
    usage: Optional[dict] = None,
) -> list[Delta]:
    """Single-turn script: assistant calls one tool and stops for result."""
    deltas: list[Delta] = []
    if leading_text:
        deltas.append(TextDelta(text=leading_text))
    deltas.append(
        ToolUseDelta(id=tool_id, name=tool_name, input_partial=tool_input)
    )
    deltas.append(TurnEnd(stop_reason="tool_use", usage=usage or {}))
    return deltas
