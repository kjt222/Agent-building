"""Unit tests for the high-level obsidian_add_formula_annotation tool.

These exercise the composing logic directly (no real Obsidian / PDF): the
target_xy fallback places the annotation, and the LaTeX SVG is baked via the
real write_elements path so we can assert the formula renders for real.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.tools_capability.obsidian.excalidraw_io import (
    encode_fence,
    read_canvas_file,
)
from agent.tools_capability.obsidian.formula_annotation import (
    add_formula_annotation,
    _wrap_text,
)


def _empty_canvas(tmp_path: Path) -> Path:
    payload = {
        "type": "excalidraw", "version": 2, "source": "test",
        "elements": [], "appState": {}, "files": {},
    }
    text = (
        "---\nexcalidraw-plugin: parsed\n---\n# Excalidraw Data\n## Drawing\n"
        f"```compressed-json\n{encode_fence(payload)}\n```\n%%\n"
    )
    p = tmp_path / "note.excalidraw.md"
    p.write_text(text, encoding="utf-8")
    return p


def _read_scene(p: Path) -> dict:
    data, _ = read_canvas_file(p.read_text(encoding="utf-8"))
    return data


def test_target_xy_creates_grouped_rendered_annotation(tmp_path):
    p = _empty_canvas(tmp_path)
    res = add_formula_annotation(
        canvas_path=p,
        latex=r"x_i = \left(x_i^{H}, f_H^{R_1}(x_i^{H})\right)",
        explanation="这是公式 (1)，它构造跨本体对齐的末端位姿元组，作为对比学习的监督信号。",
        target_xy=(500.0, -1400.0),
        side="left",
    )
    assert res.ok, res.error
    assert res.anchored_to == "target_xy"
    assert res.arrow_target == (500.0, -1400.0)
    assert len(res.element_ids) == 3

    scene = _read_scene(p)
    by_id = {e["id"]: e for e in scene["elements"]}
    assert len(scene["elements"]) == 3
    formula = next(e for e in scene["elements"] if e["type"] == "image")
    text_el = next(e for e in scene["elements"] if e["type"] == "text")
    arrow = next(e for e in scene["elements"] if e["type"] == "arrow")

    # All three share exactly the returned group id.
    for el in (formula, text_el, arrow):
        assert el["groupIds"] == [res.group_id]
        # container-strategy rule: never both group and frame
        assert not el.get("frameId")

    # Formula image renders for real (baked SVG dataURL present).
    fid = formula["fileId"]
    dataurl = scene["files"][fid]["dataURL"]
    assert dataurl.startswith("data:image/svg+xml;base64,")
    assert len(dataurl) > 500
    assert formula["customData"]["latex_source"].startswith("x_i =")

    # Text sits BELOW the formula, not overlapping it.
    assert text_el["y"] >= formula["y"] + formula["height"]

    # Arrow ends at the target point.
    end = arrow["points"][-1]
    assert pytest.approx(arrow["x"] + end[0], abs=0.5) == 500.0
    assert pytest.approx(arrow["y"] + end[1], abs=0.5) == -1400.0

    # side="left" → block is left of the target.
    assert formula["x"] < 500.0


def test_left_vs_right_side_places_block_on_correct_side(tmp_path):
    dl = tmp_path / "l"; dl.mkdir()
    dr = tmp_path / "r"; dr.mkdir()
    p_left = _empty_canvas(dl)
    p_right = _empty_canvas(dr)

    common = dict(
        latex="a^2 + b^2 = c^2",
        explanation="勾股定理。",
        target_xy=(0.0, 0.0),
    )
    left = add_formula_annotation(canvas_path=p_left, side="left", **common)
    right = add_formula_annotation(canvas_path=p_right, side="right", **common)
    assert left.ok and right.ok
    assert left.formula_xy[0] < 0.0   # left of target
    assert right.formula_xy[0] > 0.0  # right of target


def test_no_anchor_and_no_target_is_actionable_error(tmp_path):
    p = _empty_canvas(tmp_path)
    res = add_formula_annotation(
        canvas_path=p, latex="x", explanation="y",
    )
    assert res.ok is False
    assert "target_xy" in (res.error or "")
    # nothing was written
    assert len(_read_scene(p)["elements"]) == 0


def test_missing_pdf_anchor_falls_back_to_target_xy(tmp_path):
    """anchor_query that can't resolve (no embedded PDF) must not crash —
    it falls back to target_xy when provided."""
    p = _empty_canvas(tmp_path)
    res = add_formula_annotation(
        canvas_path=p, latex="x^2", explanation="说明",
        anchor_query="(99)", target_xy=(10.0, 20.0),
    )
    assert res.ok, res.error
    assert res.anchored_to == "target_xy"


def test_wrap_text_hard_wraps_and_sizes():
    wrapped, w, h = _wrap_text("一二三四五六七八九十", max_width_px=40.0, font_size=20)
    # 40px / 20px = 2 CJK chars per line → 5 lines
    assert wrapped.count("\n") == 4
    assert h > w  # tall narrow box
    # single short line: no wrapping
    w1, _, h1 = _wrap_text("hi", max_width_px=400.0, font_size=20)
    assert "\n" not in w1
