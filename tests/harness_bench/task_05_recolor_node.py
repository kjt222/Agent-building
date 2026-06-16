"""P18-B — task 5: recolor one node by id (replace_by_id, not append).

A canvas with three rectangles. The agent must change the stroke color of the
rectangle whose id is ``target`` to red (#ff0000) using
``obsidian_write_excalidraw_elements`` in replace_by_id mode, leaving the
other two rectangles' colors and the element count unchanged.

This task fails the naive "append a new red rectangle" strategy: the verifier
requires the element COUNT to stay constant and the *specific* id to change.

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

_TARGET = "rect-target"
_RED = {"#ff0000", "#f00", "red", "#e03131", "#ff0000ff"}


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t05_"))
    canvas = workdir / "board.excalidraw.md"
    elements = [
        make_element("rect-a", "rectangle", x=0, y=0, strokeColor="#1e1e1e"),
        make_element(_TARGET, "rectangle", x=200, y=0, strokeColor="#1e1e1e"),
        make_element("rect-c", "rectangle", x=400, y=0, strokeColor="#1e1e1e"),
    ]
    canvas.write_text(build_canvas_md(elements), encoding="utf-8")
    prompt = (
        f"In the Excalidraw canvas at `{canvas}`, change ONLY the rectangle "
        f"with id `{_TARGET}` so its stroke color is red. Keep every other "
        f"element exactly as-is, and do not add or remove any elements — the "
        f"canvas must still have exactly three rectangles."
    )
    return {"workdir": str(workdir), "canvas": str(canvas), "_prompt": prompt}


def verify(outcome, state) -> tuple[bool, str]:
    canvas = Path(state["canvas"])
    try:
        data, _ = read_canvas_file(canvas.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"canvas no longer decodes: {exc}"

    live = live_elements(data)
    if len(live) != 3:
        return False, f"expected exactly 3 live elements, found {len(live)}"

    target = element_by_id(data, _TARGET)
    if target is None or target.get("isDeleted"):
        return False, f"target {_TARGET!r} is missing or deleted"
    stroke = str(target.get("strokeColor") or "").strip().lower()
    if stroke not in _RED:
        return False, f"target stroke is {stroke!r}, expected a red value"

    for rid in ("rect-a", "rect-c"):
        other = element_by_id(data, rid)
        if other is None:
            return False, f"element {rid!r} disappeared"
        if str(other.get("strokeColor") or "").strip().lower() != "#1e1e1e":
            return False, f"element {rid!r} stroke was changed; it must stay default"
    return True, f"recolored {_TARGET!r} to red; other two unchanged"


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
