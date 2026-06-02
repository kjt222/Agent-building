"""Per-conversation interrupt registry (P12.1).

The chat UI uses two endpoints:

- ``POST /api/agent_chat_v2`` streams a single agent run.
- ``POST /api/conversations/{conv_id}/interrupt`` is a side channel that
  sets the cancel event for whichever run is currently active under that
  conversation, so the user can stop a long-running model stream or tool
  call without dropping the SSE connection.

This module owns the ``conversation_id -> asyncio.Event`` map. It is
thread-/task-safe and lazy: ``acquire_event`` creates the event when the
run starts, and ``release_event`` clears it when the run finishes. The
interrupt endpoint can ``set_interrupt`` whether or not a run is active —
if no run is active the call is a no-op.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Dict


_EVENTS: Dict[str, asyncio.Event] = {}
_LOCK = threading.Lock()


def _key(conversation_id: str | None) -> str:
    cid = (conversation_id or "").strip()
    return cid or "default"


def acquire_event(conversation_id: str | None) -> asyncio.Event:
    """Get-or-create the cancel event for ``conversation_id``.

    Called by the SSE producer before starting ``AgentLoop.run``. The
    returned event is freshly cleared so a stale interrupt from a previous
    run cannot leak into this one.
    """
    cid = _key(conversation_id)
    with _LOCK:
        event = _EVENTS.get(cid)
        if event is None:
            event = asyncio.Event()
            _EVENTS[cid] = event
        else:
            event.clear()
        return event


def release_event(conversation_id: str | None) -> None:
    """Drop the event entry after a run finishes."""
    cid = _key(conversation_id)
    with _LOCK:
        _EVENTS.pop(cid, None)


def set_interrupt(conversation_id: str | None) -> bool:
    """Signal the currently active run to stop.

    Returns ``True`` if an active run was found and signalled, ``False``
    otherwise. The caller (HTTP endpoint) uses this to decide whether to
    return 200 or 404.
    """
    cid = _key(conversation_id)
    with _LOCK:
        event = _EVENTS.get(cid)
    if event is None:
        return False
    event.set()
    return True


def is_active(conversation_id: str | None) -> bool:
    """Tests/diagnostics: is a run currently registered?"""
    cid = _key(conversation_id)
    with _LOCK:
        return cid in _EVENTS


def reset_all() -> None:
    """Drop every entry (tests only)."""
    with _LOCK:
        _EVENTS.clear()
