"""P18-B — task 7: remove a frame but keep its children (replace_by_id).

Reconstructed from ``_tmp_remove_frame.py``: a frame named "(6)(7)" wraps two
child elements. The agent must remove the frame's visible border while
KEEPING the two children on the canvas — i.e. mark the frame deleted (or drop
it) and clear ``frameId`` on each child so they survive as free elements.

The verifier enforces the reference end state: frame gone from the live set,
both children still live, neither child still pointing at the frame.

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
    make_frame,
    read_canvas_file,
)

PROMPT = ""
MODE = "full-access"
TIMEOUT_S = 300.0

_FRAME = "frame-67"
_CHILDREN = ("child-1", "child-2")


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t07_"))
    canvas = workdir / "framed.excalidraw.md"
    elements = [
        make_frame(_FRAME, "(6)(7)"),
        make_element("child-1", "rectangle", x=20, y=20, frameId=_FRAME),
        make_element("child-2", "ellipse", x=200, y=20, frameId=_FRAME),
    ]
    canvas.write_text(build_canvas_md(elements), encoding="utf-8")
    prompt = (
        f"In the Excalidraw canvas at `{canvas}`, remove the frame named "
        f"\"(6)(7)\" (id `{_FRAME}`) so its border no longer shows, but KEEP "
        f"its two child elements on the canvas as free-standing shapes. The "
        f"children must remain; only the frame container should go away."
    )
    return {"workdir": str(workdir), "canvas": str(canvas), "_prompt": prompt}


def verify(outcome, state) -> tuple[bool, str]:
    canvas = Path(state["canvas"])
    try:
        data, _ = read_canvas_file(canvas.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"canvas no longer decodes: {exc}"

    # Frame must be gone from the live set (deleted or removed entirely).
    frame = element_by_id(data, _FRAME)
    if frame is not None and not frame.get("isDeleted"):
        return False, "frame is still present/live; its border was not removed"

    live_ids = {e.get("id") for e in live_elements(data)}
    for cid in _CHILDREN:
        child = element_by_id(data, cid)
        if child is None or child.get("isDeleted"):
            return False, f"child {cid!r} was lost; children must survive"
        if cid not in live_ids:
            return False, f"child {cid!r} is not live"
        if child.get("frameId") == _FRAME:
            return False, f"child {cid!r} still references the removed frame"
    return True, "frame removed; both children kept and detached from frame"


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
