"""P18-B — task 4: append a labeled node to an Excalidraw canvas.

A canvas with two existing rectangles. The agent must ADD one new text
element reading "Reviewed" (a new node, not a replacement) via
``obsidian_write_excalidraw_elements`` in append mode, leaving the two
original elements untouched.

INTENT — external vault, full-access mode (the fixture lives under %TEMP%
like a real Obsidian vault outside the workspace).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from tests.harness_bench._excalidraw_fixtures import (
    build_canvas_md,
    element_by_id,
    live_elements,
    make_element,
    read_canvas_file,
)

PROMPT = ""
MODE = "full-access"
TIMEOUT_S = 300.0

_LABEL = "Reviewed"


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t04_"))
    canvas = workdir / "diagram.excalidraw.md"
    elements = [
        make_element("rect-a", "rectangle", x=0, y=0),
        make_element("rect-b", "rectangle", x=200, y=0),
    ]
    canvas.write_text(build_canvas_md(elements), encoding="utf-8")
    prompt = (
        f"In the Excalidraw canvas at `{canvas}`, ADD a new text element whose "
        f"text is exactly \"{_LABEL}\". Do not delete, move, or modify the two "
        f"existing rectangles — only append the new label as an additional "
        f"element."
    )
    return {"workdir": str(workdir), "canvas": str(canvas), "_prompt": prompt}


def verify(outcome, state) -> tuple[bool, str]:
    canvas = Path(state["canvas"])
    try:
        data, _ = read_canvas_file(canvas.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"canvas no longer decodes: {exc}"

    live = live_elements(data)
    # Originals must survive.
    for rid in ("rect-a", "rect-b"):
        el = element_by_id(data, rid)
        if el is None or el.get("isDeleted"):
            return False, f"original element {rid!r} was removed"

    labels = [
        e for e in live
        if str(e.get("text") or e.get("rawText") or "").strip() == _LABEL
    ]
    if not labels:
        return False, f"no new element with text {_LABEL!r} was added"
    if len(live) < 3:
        return False, f"expected >=3 live elements after append, found {len(live)}"
    return True, f"appended {_LABEL!r} label; both rectangles preserved"


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
