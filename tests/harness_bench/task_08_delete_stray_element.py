"""P18-B — task 8: delete one stray element, keep the rest (replace_by_id).

A canvas with three rectangles and one stray arrow left over from an earlier
edit. The agent must remove ONLY the stray arrow (mark it deleted / drop it),
leaving the three rectangles intact.

The verifier requires the arrow gone AND all three rectangles still live, so a
"delete everything and redraw" shortcut fails.

INTENT — external vault, full-access mode.
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

_STRAY = "arrow-stray"
_KEEP = ("rect-a", "rect-b", "rect-c")


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t08_"))
    canvas = workdir / "cleanup.excalidraw.md"
    elements = [
        make_element("rect-a", "rectangle", x=0, y=0),
        make_element("rect-b", "rectangle", x=200, y=0),
        make_element("rect-c", "rectangle", x=400, y=0),
        make_element(_STRAY, "arrow", x=120, y=120, width=80, height=10),
    ]
    canvas.write_text(build_canvas_md(elements), encoding="utf-8")
    prompt = (
        f"In the Excalidraw canvas at `{canvas}`, delete ONLY the stray arrow "
        f"element (id `{_STRAY}`). Keep all three rectangles exactly as they "
        f"are — do not delete or redraw them."
    )
    return {"workdir": str(workdir), "canvas": str(canvas), "_prompt": prompt}


def verify(outcome, state) -> tuple[bool, str]:
    canvas = Path(state["canvas"])
    try:
        data, _ = read_canvas_file(canvas.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"canvas no longer decodes: {exc}"

    arrow = element_by_id(data, _STRAY)
    if arrow is not None and not arrow.get("isDeleted"):
        return False, "the stray arrow is still live; it was not deleted"

    live_ids = {e.get("id") for e in live_elements(data)}
    for rid in _KEEP:
        rect = element_by_id(data, rid)
        if rect is None or rect.get("isDeleted") or rid not in live_ids:
            return False, f"rectangle {rid!r} was lost; it must be kept"

    arrows_live = [e for e in live_elements(data) if e.get("type") == "arrow"]
    if arrows_live:
        return False, f"an arrow is still live: {[e.get('id') for e in arrows_live]}"
    return True, "stray arrow deleted; all three rectangles kept"


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
