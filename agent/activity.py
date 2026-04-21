"""Activity event emitter for real-time UI updates."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, asdict
from typing import AsyncGenerator, Optional


def format_sse(event: str, data: dict | str) -> str:
    """Unified SSE formatting function."""
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


@dataclass
class ActivityEvent:
    id: str
    type: str
    title: str
    detail: str
    status: str  # start | update | done | error
    ts: float
    meta: dict


class ActivityCollector:
    """Collects and streams activity events for a single request."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.events: dict[str, ActivityEvent] = {}
        self.start_time = time.time()
        self.done = False

    def emit(
        self,
        key: str,
        type: str,
        title: str,
        detail: str = "",
        status: str = "done",
        meta: Optional[dict] = None,
    ) -> None:
        """Emit an activity event, immediately format and enqueue."""
        event = ActivityEvent(
            id=f"{self.request_id}_{key}",
            type=type,
            title=title,
            detail=detail,
            status=status,
            ts=time.time() * 1000,
            meta=meta or {},
        )
        self.events[key] = event
        sse = format_sse("activity", asdict(event))
        self.queue.put_nowait(sse)

    def emit_token(self, text: str) -> None:
        """Emit a token event for streaming text."""
        sse = format_sse("token", {"text": text})
        self.queue.put_nowait(sse)

    def emit_done(self, extra: Optional[dict] = None) -> None:
        """Emit done event and signal stream end."""
        data = {"total_time_ms": self.total_time_ms()}
        if extra:
            data.update(extra)
        sse = format_sse("done", data)
        self.queue.put_nowait(sse)
        self.queue.put_nowait(None)  # End signal
        self.done = True

    def emit_error(self, error: str) -> None:
        """Emit an error event."""
        self.emit("error", "error", "Error", error, status="error")
        self.emit_done({"error": error})

    def emit_ping(self) -> None:
        """Emit heartbeat (SSE comment format)."""
        self.queue.put_nowait(": ping\n\n")

    def total_time_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)

    async def stream(self) -> AsyncGenerator[str, None]:
        """Stream from queue, exit on None."""
        while not self.done:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=0.1)
                if item is None:  # End signal
                    break
                yield item
            except asyncio.TimeoutError:
                continue
