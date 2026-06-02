"""Shared lz-string / JSON helpers for the single-file Excalidraw .md format.

An Obsidian Excalidraw plugin canvas file has the structure:

    ---
    excalidraw-plugin: parsed
    tags: [excalidraw]
    ---
    # Excalidraw Data
    ## Text Elements
    <plugin-rendered text elements, may be empty>

    ## Element Links
    <id>: <link or path>

    ## Drawing
    ```compressed-json
    <lz-string base64 of the canvas JSON, often with embedded newlines>
    ```
    %%

The ``## Drawing`` fence is the source of truth — ``## Text Elements`` is
a human-readable mirror the plugin generates. The compressed JSON inside
the fence wraps lines at ~80 chars; the Python lzstring library chokes on
the embedded newlines, so we strip whitespace before decoding.

These helpers stay deliberately format-only — they do NOT touch the
filesystem, REST API, or any Excalidraw rendering logic. That separation
makes them trivially unit-testable.
"""

from __future__ import annotations

import json
import re
from typing import Any

import lzstring

_FENCE_RE = re.compile(r"```compressed-json\s*\n([\s\S]+?)\n```")


def decode_fence(fence_text: str) -> dict[str, Any]:
    """Decode the inner contents of a `compressed-json` fence.

    Pass the raw text between the triple-backticks (with or without
    embedded line breaks); we strip whitespace before lz-string decode.

    The Python lzstring library raises a bare ``KeyError`` on characters
    that aren't valid base64 — convert those to ``ValueError`` so callers
    only need a single except clause.
    """
    cleaned = re.sub(r"\s+", "", fence_text)
    try:
        decoded = lzstring.LZString().decompressFromBase64(cleaned)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid lz-string base64: {exc}") from exc
    if not decoded:
        raise ValueError("lz-string decompress returned empty")
    try:
        return json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise ValueError(f"decoded payload is not JSON: {exc}") from exc


def encode_fence(data: dict[str, Any]) -> str:
    """Encode canvas dict to the lz-string base64 form for the fence body.

    Output has no line wrapping; the Excalidraw plugin reads it back fine
    either way.
    """
    payload = json.dumps(data, separators=(",", ":"))
    return lzstring.LZString().compressToBase64(payload)


def read_canvas_file(text: str) -> tuple[dict[str, Any], tuple[int, int]]:
    """Locate the compressed-json fence in a canvas .md and decode it.

    Returns ``(decoded_dict, (start, end))`` where ``start/end`` are the
    fence-body offsets in the source text (suitable for splicing). Raises
    ``ValueError`` if no fence is found.
    """
    m = _FENCE_RE.search(text)
    if not m:
        raise ValueError("no compressed-json fence in file")
    return decode_fence(m.group(1)), m.span(1)


def write_canvas_data(original_text: str, data: dict[str, Any]) -> str:
    """Return ``original_text`` with the canvas dict re-encoded in the
    compressed-json fence (preserves frontmatter, text elements, links
    sections — everything outside the fence is left untouched).
    """
    _, (s, e) = read_canvas_file(original_text)
    return original_text[:s] + encode_fence(data) + original_text[e:]


def element_bbox(elements: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    """Return (min_x, min_y, max_x, max_y) over non-deleted elements.

    Returns ``(0, 0, 0, 0)`` if elements list is empty / no coords.
    """
    xs: list[float] = []
    ys: list[float] = []
    for el in elements:
        if el.get("isDeleted"):
            continue
        x = el.get("x")
        y = el.get("y")
        if x is None or y is None:
            continue
        w = float(el.get("width", 0))
        h = float(el.get("height", 0))
        xs.extend([float(x), float(x) + w])
        ys.extend([float(y), float(y) + h])
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))
