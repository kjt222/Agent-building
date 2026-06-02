"""Tests for the Excalidraw PNG renderer (P14.3.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from PIL import Image

from agent.acceptance.renderers.excalidraw_renderer import (
    render_excalidraw_file,
    render_excalidraw_scene,
)


def _scene(elements):
    return {"type": "excalidraw", "elements": elements, "files": {}}


def test_renders_blank_when_empty(tmp_path):
    out = tmp_path / "out.png"
    meta = render_excalidraw_scene(_scene([]), out)
    assert out.exists()
    assert meta["empty"] is True
    img = Image.open(out)
    assert img.size == (64, 64)


def test_renders_basic_scene_with_correct_size(tmp_path):
    out = tmp_path / "out.png"
    elements = [
        {"id": "f1", "type": "frame", "x": 0, "y": 0,
         "width": 400, "height": 200, "name": "公式 (6)"},
        {"id": "t1", "type": "text", "x": 10, "y": 10,
         "width": 380, "height": 30, "text": "hello"},
        {"id": "i1", "type": "image", "x": 10, "y": 50,
         "width": 380, "height": 60,
         "customData": {"latex_source": "x^2 + y^2"}},
    ]
    meta = render_excalidraw_scene(_scene(elements), out)
    assert out.exists()
    assert meta["rendered"] is True
    assert meta["elements"] == 3
    assert meta["by_type"].get("frame") == 1
    assert meta["by_type"].get("text") == 1
    assert meta["by_type"].get("image") == 1
    img = Image.open(out)
    # Should be padded around the scene bbox
    assert img.size[0] >= 400
    assert img.size[1] >= 200


def test_render_from_file_reads_plain_json(tmp_path):
    p = tmp_path / "n.excalidraw.md"
    body = (
        "# title\n%%\n"
        + json.dumps(_scene([
            {"id": "r", "type": "rectangle", "x": 0, "y": 0,
             "width": 100, "height": 100}
        ]))
        + "\n%%\n"
    )
    p.write_text(body, encoding="utf-8")
    out = tmp_path / "rendered.png"
    meta = render_excalidraw_file(p, out)
    assert meta["rendered"] is True
    assert meta["kind"] == "plain-json"
    assert out.exists()


def test_render_from_file_handles_unparseable(tmp_path):
    p = tmp_path / "broken.md"
    p.write_text("nothing here", encoding="utf-8")
    out = tmp_path / "rendered.png"
    meta = render_excalidraw_file(p, out)
    assert meta["rendered"] is False
    assert "parse_error" in meta
    assert not out.exists()


def test_render_clamps_to_max_size(tmp_path):
    out = tmp_path / "out.png"
    elements = [{
        "id": "huge", "type": "rectangle",
        "x": 0, "y": 0, "width": 100_000, "height": 100_000,
    }]
    meta = render_excalidraw_scene(_scene(elements), out, max_size=(800, 600))
    img = Image.open(out)
    assert img.size[0] <= 800
    assert img.size[1] <= 600
    assert meta["scale"] < 1.0
