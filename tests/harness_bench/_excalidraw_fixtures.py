"""Shared fixture helpers for the P18-B Excalidraw harness tasks (04-08).

Builds a self-contained single-file Excalidraw ``.excalidraw.md`` whose
``## Drawing`` compressed-json fence the agent mutates via
``obsidian_write_excalidraw_elements`` (append / replace_by_id). Verifiers
decode the fence back with ``agent.tools_capability.obsidian.excalidraw_io``
and assert on the end state.

These tasks were authored from the README P18-B tier description after the
D-drive-format recovery; the original task files (04-08) were created in an
earlier rotated session and are not in the available transcript.
"""

from __future__ import annotations

import time
from typing import Any

from agent.tools_capability.obsidian.excalidraw_io import (  # re-exported for verifiers
    decode_fence,
    element_bbox,
    encode_fence,
    read_canvas_file,
)

__all__ = [
    "decode_fence",
    "element_bbox",
    "read_canvas_file",
    "make_element",
    "make_frame",
    "build_canvas_md",
    "live_elements",
    "element_by_id",
]


def make_element(
    el_id: str,
    el_type: str = "rectangle",
    *,
    x: float = 0.0,
    y: float = 0.0,
    width: float = 100.0,
    height: float = 60.0,
    **extra: Any,
) -> dict[str, Any]:
    """Build a minimally-valid Excalidraw element dict.

    ``extra`` overrides / adds any field (strokeColor, text, link, frameId,
    groupIds, isDeleted, ...). The defaults mirror what the Excalidraw plugin
    emits so the L2 oracle treats the canvas as well-formed.
    """
    el: dict[str, Any] = {
        "id": el_id,
        "type": el_type,
        "x": float(x),
        "y": float(y),
        "width": float(width),
        "height": float(height),
        "angle": 0,
        "strokeColor": "#1e1e1e",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 2,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "groupIds": [],
        "frameId": None,
        "roundness": None,
        "seed": 1,
        "version": 1,
        "versionNonce": 1,
        "isDeleted": False,
        "boundElements": None,
        "updated": 1,
        "link": None,
        "locked": False,
    }
    el.update(extra)
    return el


def make_frame(frame_id: str, name: str, **extra: Any) -> dict[str, Any]:
    """Build a frame element (a named container with its own border)."""
    el = make_element(
        frame_id, "frame", x=-20.0, y=-20.0, width=400.0, height=300.0, name=name
    )
    el.update(extra)
    return el


def build_canvas_md(
    elements: list[dict[str, Any]],
    *,
    app_state: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    element_links: str = "",
) -> str:
    """Wrap canvas elements into the full single-file ``.excalidraw.md`` text."""
    data: dict[str, Any] = {
        "type": "excalidraw",
        "version": 2,
        "source": "harness_bench",
        "elements": elements,
        "appState": app_state or {"gridSize": None, "viewBackgroundColor": "#ffffff"},
        "files": files or {},
    }
    links_block = f"\n## Element Links\n{element_links}\n" if element_links else ""
    return (
        "---\n"
        "excalidraw-plugin: parsed\n"
        "tags: [excalidraw]\n"
        "---\n"
        "# Excalidraw Data\n"
        "## Text Elements\n"
        f"{links_block}"
        "\n## Drawing\n"
        "```compressed-json\n"
        f"{encode_fence(data)}\n"
        "```\n"
        "%%\n"
    )


def live_elements(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-deleted elements from a decoded canvas dict."""
    return [e for e in data.get("elements", []) if not e.get("isDeleted")]


def element_by_id(data: dict[str, Any], el_id: str) -> dict[str, Any] | None:
    for e in data.get("elements", []):
        if e.get("id") == el_id:
            return e
    return None


def now_ms() -> int:
    return int(time.time() * 1000)
