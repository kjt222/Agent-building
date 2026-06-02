"""Mirror-mode path guard for obsidian_* capability tools.

When a smoke/test runner spins up a vault MIRROR (an isolated copy of a
real Obsidian vault under ``tests/_vault_mirror/``), the runner sets the
environment variable ``OBSIDIAN_MIRROR_ROOT`` to the mirror directory.

Past V4 / GLM runs ignored mirror-mode prompts and globbed the host
filesystem to discover the real vault, then wrote directly to it. The
runner's snapshot/rollback safety net catches that AFTER the fact, but
the real canvas still gets mutated mid-run and we're trusting rollback
to not lose data.

This module is the second line of defense: while a mirror is active,
any obsidian_* tool call whose ``canvas_path`` (or REST request URL)
escapes the mirror is rejected with a clear error pointing the model
back at the mirror path.

Outside of mirror mode (no env var set), guards are no-ops.
"""

from __future__ import annotations

import os
from pathlib import Path


def mirror_root() -> Path | None:
    """Return the active mirror root, or None if mirror mode is off."""
    raw = os.environ.get("OBSIDIAN_MIRROR_ROOT")
    if not raw:
        return None
    try:
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def guard_canvas_path(path: Path) -> str | None:
    """Return an error message if ``path`` is outside the mirror; else None.

    The caller should turn the message into a ToolResultBlock with
    is_error=True. We do NOT raise — the model gets a structured error
    and a hint about where it SHOULD write.
    """
    root = mirror_root()
    if root is None:
        return None
    try:
        resolved = path.expanduser().resolve()
    except Exception as exc:
        return f"path guard: cannot resolve {path}: {exc}"
    try:
        resolved.relative_to(root)
    except ValueError:
        return (
            f"path guard (mirror mode): {resolved} is outside the mirror "
            f"root {root}. This test run is sandboxed to the mirror — "
            f"do NOT touch the real vault. If you intended to edit the "
            f"task canvas, its path inside the mirror is under {root}."
        )
    return None


def rest_disabled_reason() -> str | None:
    """When mirror mode is on the Obsidian REST API is not reachable.

    The mirror is a flat directory copy, not a running Obsidian instance.
    Any call into ``rest_client`` (refresh_note, /open, /commands, ...)
    should short-circuit with this explanation so the model stops trying.
    """
    if mirror_root() is None:
        return None
    return (
        "obsidian REST API is not available in this test run (mirror "
        "mode — no Obsidian process). Write to the mirror file directly; "
        "the test harness will inspect the file on disk."
    )
