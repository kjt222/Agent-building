"""Shared Excalidraw markdown / JSON decode helpers (P14.2).

Both the L2 oracle and the L3 renderer need to read an .excalidraw.md file
and recover the JSON scene. Logic lifted from the smoke runner so we have a
single source of truth — if Obsidian changes the fence format again, only
this file changes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_FENCE_RE = re.compile(r"```compressed-json\s*\n(.*?)\n```", re.DOTALL)


def decode_excalidraw_text(text: str) -> tuple[dict | None, str | None, str]:
    """Return (data, error, kind). kind ∈ {"compressed-json","plain-json","none"}."""
    m = _FENCE_RE.search(text)
    if m is not None:
        try:
            import lzstring  # type: ignore
        except Exception as exc:
            return None, f"lzstring import failed: {exc}", "compressed-json"
        body = re.sub(r"\s+", "", m.group(1))
        try:
            decoded = lzstring.LZString().decompressFromBase64(body)
            if not decoded:
                return None, "lz-string returned empty", "compressed-json"
            return json.loads(decoded), None, "compressed-json"
        except Exception as exc:
            return None, f"compressed-json decode: {exc}", "compressed-json"
    m_open = text.find("%%")
    m_close = text.find("%%", m_open + 2) if m_open >= 0 else -1
    if 0 <= m_open < m_close:
        block = text[m_open + 2 : m_close].strip()
        try:
            return json.loads(block), None, "plain-json"
        except Exception as exc:
            return None, f"plain-json parse: {exc}", "plain-json"
    return None, "no fence and no %% block", "none"


def load_excalidraw(path: Path) -> tuple[dict | None, str | None, str]:
    """Read a .excalidraw[.md] file from disk and decode it."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return None, f"read failed: {exc}", "none"
    # Bare .excalidraw is often just JSON on its own.
    if path.suffix.lower() == ".excalidraw":
        try:
            return json.loads(text), None, "plain-json"
        except Exception:
            pass  # fall through to fence search
    return decode_excalidraw_text(text)


def iter_latex_elements(scene: dict[str, Any]):
    """Yield elements that carry a customData.latex_source string."""
    for el in scene.get("elements") or []:
        ls = (el.get("customData") or {}).get("latex_source")
        if isinstance(ls, str) and ls.strip():
            yield el
