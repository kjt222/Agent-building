"""obsidian.refresh_note tool — single-attempt force re-read.

PROVEN-WORKING sequence (P14.6, 2026-05-21):

    POST /open/<path>                 # make target file active tab
    sleep(open_delay_s)
    POST /commands/workspace:close/   # destroy plugin's in-memory canvas state
    sleep(close_delay_s)
    POST /open/<path>                 # plugin re-reads .md from disk

Why this specific sequence: the Excalidraw plugin caches a parsed canvas
in memory and ignores external file writes. Closing the tab destroys
that cache; re-opening forces a fresh disk read. Setting the file as
active *before* closing ensures we close the right tab, not whatever
the user happened to be looking at.

Tools-as-meta-capability discipline (per user 2026-05-21):
- This is a single-attempt primitive. It does NOT implement
  verify-then-retry internally — that's the model's responsibility.
- It returns a structured result the model can read (timings + REST
  status codes + the post-reopen active-note size from /active/) so the
  model can spot an empty-buffer failure mode without needing a separate
  verify call.
- The prompt MUST NOT instruct the model to "call refresh, then verify,
  then retry if needed." That orchestration must emerge from the model's
  own reading of the verify oracle output.

This module is callable directly from Python (for unit tests / smoke
runners) AND wraps into a ``_ToolBase``-shaped class so the agent loop
can register it via the existing tools_v2 factory protocol.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent.tools_capability.obsidian.rest_client import (
    ObsidianRestClient,
    keyring_ref_for_vault,
)


@dataclass
class RefreshResult:
    """What the tool returns to the model. Stable shape so the model can
    pattern-match on these fields without a parsing step."""

    ok: bool
    elapsed_ms: int
    open1_status: int = 0
    close_status: int = 0
    open2_status: int = 0
    active_size_after_ms: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def refresh_note_sync(
    *,
    vault_root: Path,
    canvas_path: Path,
    client: ObsidianRestClient | None = None,
    open_delay_s: float = 0.5,
    close_delay_s: float = 0.8,
    render_delay_s: float = 2.0,
) -> RefreshResult:
    """Synchronous implementation. The async tool wraps this in a thread.

    ``open_delay_s`` and ``close_delay_s`` are empirical (see probe
    history). ``render_delay_s`` is how long to wait after the second
    /open before treating the canvas as fully rendered.
    """
    start = time.monotonic()
    if client is None:
        client = ObsidianRestClient(keyring_ref=keyring_ref_for_vault(str(vault_root)))
    try:
        rel = canvas_path.relative_to(vault_root).as_posix()
    except ValueError:
        return RefreshResult(
            ok=False, elapsed_ms=0,
            error=f"canvas_path {canvas_path} is not under vault_root {vault_root}",
        )

    # Step 1: focus the target tab so workspace:close hits the right one.
    try:
        client.open_file(rel)
    except Exception as exc:
        return RefreshResult(ok=False, elapsed_ms=_ms_since(start),
                             error=f"open1 failed: {exc}")
    time.sleep(open_delay_s)

    # Step 2: close → destroys plugin's in-memory canvas state.
    try:
        client.execute_command("workspace:close")
    except Exception as exc:
        return RefreshResult(ok=False, elapsed_ms=_ms_since(start),
                             error=f"close failed: {exc}")
    time.sleep(close_delay_s)

    # Step 3: re-open → plugin reads from disk fresh.
    try:
        client.open_file(rel)
    except Exception as exc:
        return RefreshResult(ok=False, elapsed_ms=_ms_since(start),
                             error=f"open2 failed: {exc}")
    time.sleep(render_delay_s)

    # Optional diagnostic: check active note size — if the buffer is
    # broken (size=0) that's a failure signal even though the requests
    # succeeded. We return the value but don't fail on it; let the model
    # decide whether to retry / try another mechanism.
    active_size: int | None = None
    try:
        info = client.active_note()
        active_size = int(info.get("stat", {}).get("size", 0))
    except Exception:
        pass

    return RefreshResult(
        ok=True,
        elapsed_ms=_ms_since(start),
        open1_status=200,
        close_status=204,
        open2_status=200,
        active_size_after_ms=active_size,
    )


def _ms_since(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


async def refresh_note_async(**kwargs) -> RefreshResult:
    """Thread-pool wrapper for use inside an asyncio event loop (tool runtime)."""
    return await asyncio.to_thread(refresh_note_sync, **kwargs)


# ---------------------------------------------------------------------------
# _ToolBase-shaped wrapper (matches the protocol in agent/tools_v2/primitives.py)
# ---------------------------------------------------------------------------

# We import lazily inside the class methods to avoid a hard coupling on
# tools_v2 at module import time — this module needs to be usable from
# unit tests that don't load the full agent runtime.


class RefreshNoteTool:
    @property
    def permission_level(self):  # type: ignore[no-untyped-def]
        from agent.core.loop import PermissionLevel
        # No file mutation — just nudges Obsidian to re-render. Safe.
        return PermissionLevel.SAFE

    name = "obsidian_refresh_note"
    description = (
        "Force Obsidian's Excalidraw plugin to drop its cached canvas state "
        "for the given file and re-read it from disk. Use after writing new "
        "elements into a .excalidraw.md so the user sees them in the open "
        "canvas tab. Single-attempt primitive — the caller is responsible "
        "for verifying that the refresh actually surfaced the change."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "vault_root": {
                "type": "string",
                "description": "Absolute path to the Obsidian vault root.",
            },
            "canvas_path": {
                "type": "string",
                "description": (
                    "Absolute path to the .excalidraw.md file to refresh. "
                    "Must be inside vault_root."
                ),
            },
        },
        "required": ["vault_root", "canvas_path"],
    }
    parallel_safe = False

    async def run(self, input: dict, ctx) -> Any:
        # Late import — runtime types live in tools_v2/core.
        from agent.core.loop import ToolResultBlock
        from agent.tools_capability.obsidian._mirror_guard import (
            guard_canvas_path,
            rest_disabled_reason,
        )

        try:
            vault_root = Path(input["vault_root"]).expanduser().resolve()
            canvas_path = Path(input["canvas_path"]).expanduser().resolve()
        except Exception as exc:
            return ToolResultBlock(
                tool_use_id="",
                content=f"invalid input paths: {exc}",
                is_error=True,
            )
        deny = guard_canvas_path(canvas_path)
        if deny:
            return ToolResultBlock(tool_use_id="", content=deny, is_error=True)
        disabled = rest_disabled_reason()
        if disabled:
            return ToolResultBlock(tool_use_id="", content=disabled, is_error=True)
        result = await refresh_note_async(
            vault_root=vault_root, canvas_path=canvas_path
        )
        text = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
        return ToolResultBlock(
            tool_use_id="", content=text, is_error=not result.ok
        )
