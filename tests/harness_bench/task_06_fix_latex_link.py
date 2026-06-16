"""P18-B — task 6: fix a broken link on a LaTeX node (replace_by_id).

Reconstructed from the ``_tmp_fix_latex_link.py`` reference intent: a canvas
element that should point at the current note instead carries a stale
``link`` to a renamed file. The agent must repoint that one element's
``link`` to the correct target, leaving its geometry and all other elements
untouched.

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
    make_element,
    read_canvas_file,
)

PROMPT = ""
MODE = "full-access"
TIMEOUT_S = 300.0

_NODE = "latex-eq"
_STALE = "[[Old Contact Resistance Review]]"
_CORRECT = "[[Contact Resistance Review]]"


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t06_"))
    canvas = workdir / "equations.excalidraw.md"
    elements = [
        make_element("frame-bg", "rectangle", x=-40, y=-40, width=500, height=320),
        make_element(
            _NODE, "image", x=40, y=40, width=240, height=80,
            link=_STALE, fileId="tex-1",
        ),
    ]
    files = {
        "tex-1": {
            "mimeType": "image/svg+xml",
            "id": "tex-1",
            "dataURL": "data:image/svg+xml;base64,PHN2Zy8+",
            "created": 1,
        }
    }
    canvas.write_text(build_canvas_md(elements, files=files), encoding="utf-8")
    prompt = (
        f"In the Excalidraw canvas at `{canvas}`, the element with id "
        f"`{_NODE}` has a stale wiki link `{_STALE}` that points to a file "
        f"that was renamed. Repoint ONLY that element's link to "
        f"`{_CORRECT}`. Do not move or resize it and do not touch any other "
        f"element."
    )
    return {"workdir": str(workdir), "canvas": str(canvas), "_prompt": prompt}


def verify(outcome, state) -> tuple[bool, str]:
    canvas = Path(state["canvas"])
    try:
        data, _ = read_canvas_file(canvas.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"canvas no longer decodes: {exc}"

    node = element_by_id(data, _NODE)
    if node is None or node.get("isDeleted"):
        return False, f"node {_NODE!r} is missing or deleted"
    link = str(node.get("link") or "").strip()
    if link == _STALE:
        return False, "link is still the stale target; it was not repointed"
    if link != _CORRECT:
        return False, f"link is {link!r}, expected {_CORRECT!r}"
    # Geometry must be preserved.
    if (node.get("x"), node.get("y"), node.get("width"), node.get("height")) != (
        40.0, 40.0, 240.0, 80.0
    ):
        return False, "node geometry changed; only the link should change"
    bg = element_by_id(data, "frame-bg")
    if bg is None or bg.get("isDeleted"):
        return False, "the background rectangle was modified/removed"
    return True, f"repointed {_NODE!r} link to {_CORRECT!r}; geometry preserved"


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
