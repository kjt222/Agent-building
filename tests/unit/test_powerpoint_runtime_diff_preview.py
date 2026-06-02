"""Tests for the PowerPointRuntimeEdit structured diff builder (P12.2.2)."""

from __future__ import annotations

from agent.core.hooks import build_powerpoint_runtime_diff
from agent.core.loop import LoopConfig, LoopContext, ToolUseBlock


def _use(ops):
    return ToolUseBlock(
        id="t1",
        name="PowerPointRuntimeEdit",
        input={"path": "deck.pptx", "ops": ops},
    )


def _ctx():
    return LoopContext(config=LoopConfig())


def test_add_slide_emits_layout_row():
    use = _use([{"op": "add_slide", "layout": "Title and Content"}])
    payload = build_powerpoint_runtime_diff(use, _ctx())
    assert payload is not None
    row = payload["op_summary"][0]
    assert row["kind"] == "add_slide"
    assert row["layout"] == "Title and Content"


def test_add_text_box_carries_slide_text_bbox():
    use = _use([
        {
            "op": "add_text_box",
            "slide": 2,
            "text": "Hello world",
            "left": 100, "top": 50,
            "width": 400, "height": 80,
        }
    ])
    payload = build_powerpoint_runtime_diff(use, _ctx())
    row = payload["op_summary"][0]
    assert row["kind"] == "add_text"
    assert row["slide"] == 2
    assert row["text"] == "Hello world"
    assert row["bbox"] == [100.0, 50.0, 400.0, 80.0]


def test_add_text_box_truncates_long_text():
    use = _use([
        {
            "op": "add_text_box",
            "slide": 1,
            "text": "x" * 500,
            "left": 0, "top": 0, "width": 100, "height": 20,
        }
    ])
    row = build_powerpoint_runtime_diff(use, _ctx())["op_summary"][0]
    assert row["text"].endswith("…")
    assert len(row["text"]) <= 121


def test_add_shape_carries_type_and_bbox():
    use = _use([
        {
            "op": "add_shape",
            "slide": 3,
            "shape_type": "diamond",
            "name": "Decision1",
            "left": 10, "top": 20, "width": 100, "height": 80,
        }
    ])
    row = build_powerpoint_runtime_diff(use, _ctx())["op_summary"][0]
    assert row["kind"] == "add_shape"
    assert row["shape_type"] == "diamond"
    assert row["bbox"] == [10.0, 20.0, 100.0, 80.0]


def test_add_connector_emits_endpoints():
    use = _use([
        {
            "op": "add_connector",
            "slide": 4,
            "connector_type": "elbow",
            "x1": 0, "y1": 10, "x2": 100, "y2": 110,
        }
    ])
    row = build_powerpoint_runtime_diff(use, _ctx())["op_summary"][0]
    assert row["kind"] == "add_connector"
    assert row["connector_type"] == "elbow"
    assert row["endpoints"] == {"from": [0.0, 10.0], "to": [100.0, 110.0]}


def test_set_shape_style_only_lists_present_fields():
    use = _use([
        {
            "op": "set_shape_style",
            "slide": 1,
            "name": "Box1",
            "fill_color": "#ff0000",
            "font_size": 18,
            # line_color, font_color, bold omitted
        }
    ])
    row = build_powerpoint_runtime_diff(use, _ctx())["op_summary"][0]
    assert row["kind"] == "set_shape_style"
    assert row["fields"] == {"fill_color": "#ff0000", "font_size": 18}


def test_set_shape_geometry_emits_bbox():
    use = _use([
        {
            "op": "set_shape_geometry",
            "slide": 1,
            "name": "Box1",
            "left": 50, "top": 60, "width": 200, "height": 150,
        }
    ])
    row = build_powerpoint_runtime_diff(use, _ctx())["op_summary"][0]
    assert row["kind"] == "set_shape_geometry"
    assert row["bbox"] == [50.0, 60.0, 200.0, 150.0]


def test_get_structure_only_returns_none():
    use = _use([{"op": "get_structure"}])
    payload = build_powerpoint_runtime_diff(use, _ctx())
    assert payload is None


def test_save_only_returns_none():
    use = _use([{"op": "save"}])
    payload = build_powerpoint_runtime_diff(use, _ctx())
    assert payload is None


def test_mixed_read_only_and_mutating_drops_read_only():
    use = _use([
        {"op": "get_structure"},
        {
            "op": "add_text_box",
            "slide": 1,
            "text": "x",
            "left": 0, "top": 0, "width": 100, "height": 20,
        },
        {"op": "save"},
    ])
    payload = build_powerpoint_runtime_diff(use, _ctx())
    assert payload is not None
    assert payload["op_count"] == 1
    assert payload["op_summary"][0]["kind"] == "add_text"


def test_create_presentation_is_mutating_side_effect():
    use = _use([{"op": "create_presentation"}])
    payload = build_powerpoint_runtime_diff(use, _ctx())
    assert payload is not None
    assert payload["op_summary"][0]["kind"] == "side_effect"
