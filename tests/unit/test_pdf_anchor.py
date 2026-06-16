"""Unit tests for agent/tools_capability/obsidian/pdf_anchor.py (P14.6.11)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest

from agent.tools_capability.obsidian.excalidraw_io import encode_fence
from agent.tools_capability.obsidian.pdf_anchor import (
    AnchorResult,
    _index_canvas_pages,
    find_pdf_text_anchor,
)


def test_index_pages_from_embedded_files_section(tmp_path):
    """The Obsidian Excalidraw plugin records PDF-page embeds in the
    '## Embedded Files' section as '<fileId-sha1>: [[paper.pdf#page=N]]',
    NOT in '## Element Links'. _index_canvas_pages must resolve pages from
    there via the image element whose fileId equals the sha1 key."""
    sha1_p1 = "a" * 40
    sha1_p3 = "b" * 40
    elements = [
        {"id": "img1", "type": "image", "fileId": sha1_p1,
         "x": 10, "y": 20, "width": 700, "height": 900},
        {"id": "img3", "type": "image", "fileId": sha1_p3,
         "x": 10, "y": 2000, "width": 700, "height": 900},
    ]
    payload = {"type": "excalidraw", "version": 2, "source": "test",
               "elements": elements, "appState": {}, "files": {}}
    text = (
        "---\nexcalidraw-plugin: parsed\n---\n# Excalidraw Data\n"
        "## Embedded Files\n"
        f"{sha1_p1}: [[paper.pdf#page=1]]\n\n"
        f"{sha1_p3}: [[paper.pdf#page=3]]\n\n"
        f"## Drawing\n```compressed-json\n{encode_fence(payload)}\n```\n%%\n"
    )
    p = tmp_path / "note.excalidraw.md"
    p.write_text(text, encoding="utf-8")

    pages, pdf_filename = _index_canvas_pages(p)
    assert pdf_filename == "paper.pdf"
    assert set(pages) == {1, 3}
    assert pages[1]["id"] == "img1"
    assert pages[3]["id"] == "img3"


def test_canvas_not_found():
    out = find_pdf_text_anchor(
        canvas_path=Path("does/not/exist.md"),
        query="(6)",
    )
    assert isinstance(out, AnchorResult)
    assert out.found is False
    assert out.error and "canvas not found" in out.error
    assert out.matches == []


def _real_canvas() -> Path:
    return Path(
        r"D:\D\scientific research vault\文献阅读\SD接触\接触电阻测试"
        r"\A Comparative Evaluation of Different Test Structures for the "
        r"Extraction of Ultralow Specific Contact Resistivity A Review.md"
    )


def _real_canvas_available() -> bool:
    p = _real_canvas()
    if not p.is_file():
        return False
    # also need vault root with .obsidian
    for parent in [p, *p.parents]:
        if (parent / ".obsidian").is_dir():
            return True
    return False


@pytest.mark.skipif(
    not _real_canvas_available(),
    reason="real vault canvas not available on this machine",
)
def test_finds_formula_6_in_real_canvas():
    out = find_pdf_text_anchor(canvas_path=_real_canvas(), query="(6)")
    assert out.found is True, out.error
    assert out.pdf_path and out.pdf_path.endswith(".pdf")
    assert out.matches
    # First match should be on a real page
    m = out.matches[0]
    assert m.page >= 1
    assert len(m.page_bbox_canvas) == 4
    assert m.char_bbox_canvas[0] >= m.page_bbox_canvas[0]
    assert m.char_bbox_canvas[1] >= m.page_bbox_canvas[1]
    # Suggested insert should sit to the right of the page bbox
    sx, sy = m.suggested_insert_xy
    px, py, pw, ph = m.page_bbox_canvas
    assert sx >= px + pw, "default 'right' should place x past the page's right edge"
    assert py - 5 <= sy <= py + ph + 5, "y should be near the char vertical center"


@pytest.mark.skipif(
    not _real_canvas_available(),
    reason="real vault canvas not available on this machine",
)
def test_returns_no_match_for_nonsense_query():
    out = find_pdf_text_anchor(
        canvas_path=_real_canvas(),
        query="ZZZZ-xyzzy-nonsense-9999",
    )
    assert out.found is False
    assert out.matches == []
    # The PDF was found, just no match — error should mention the query
    assert out.error and "not found" in out.error


@pytest.mark.skipif(
    not _real_canvas_available(),
    reason="real vault canvas not available on this machine",
)
def test_insert_side_below():
    out = find_pdf_text_anchor(
        canvas_path=_real_canvas(),
        query="(6)",
        insert_side="below",
    )
    assert out.found is True
    m = out.matches[0]
    px, py, pw, ph = m.page_bbox_canvas
    sx, sy = m.suggested_insert_xy
    assert sy >= py + ph, "'below' should drop the y past the page's bottom edge"
