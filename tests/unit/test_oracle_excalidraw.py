"""Tests for the Excalidraw L2 oracle (P14.2.2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.acceptance.oracles.excalidraw import ExcalidrawOracle
from agent.acceptance.oracle import get_oracle


# Side-effect of importing oracles package registers them.
def test_oracle_is_registered():
    from agent.acceptance import oracles  # noqa: F401
    assert get_oracle("excalidraw") is not None
    assert get_oracle("excalidraw").name == "excalidraw"


def _scene(elements, files=None):
    return {"type": "excalidraw", "elements": elements, "files": files or {}}


def _write_md(tmp_path: Path, scene: dict) -> Path:
    # Plain JSON %% block — oracle reads either format.
    p = tmp_path / "note.excalidraw.md"
    body = f"%%\n{json.dumps(scene)}\n%%\n"
    p.write_text(body, encoding="utf-8")
    return p


def test_empty_path_list_returns_unknown(tmp_path):
    rep = ExcalidrawOracle().check([])
    assert rep.verdict == "unknown"


def test_unparseable_file_returns_unknown(tmp_path):
    p = tmp_path / "bad.excalidraw.md"
    p.write_text("not an excalidraw note at all", encoding="utf-8")
    rep = ExcalidrawOracle().check([p])
    assert rep.verdict == "unknown"


def test_passes_when_grouped_latex_with_valid_files(tmp_path):
    fid = "f1"
    dataurl = "data:image/svg+xml;base64," + ("A" * 1000)
    elements = [
        {
            "id": "e1", "type": "image",
            "x": 0, "y": 0, "width": 100, "height": 30,
            "groupIds": ["g1"], "fileId": fid,
            "customData": {"latex_source": "x^2"},
        },
        {
            "id": "e2", "type": "image",
            "x": 0, "y": 50, "width": 100, "height": 30,
            "groupIds": ["g1"], "fileId": fid,
            "customData": {"latex_source": "y^2"},
        },
    ]
    files = {fid: {"dataURL": dataurl}}
    p = _write_md(tmp_path, _scene(elements, files))
    rep = ExcalidrawOracle().check([p])
    assert rep.verdict == "pass", rep.findings


def test_warns_when_latex_elements_ungrouped(tmp_path):
    elements = [
        {
            "id": "e1", "type": "image",
            "x": 0, "y": 0, "width": 100, "height": 30,
            "customData": {"latex_source": "x^2"},
        },
        {
            "id": "e2", "type": "image",
            "x": 0, "y": 50, "width": 100, "height": 30,
            "customData": {"latex_source": "y^2"},
        },
    ]
    # No fileIds → no renderability failure, just grouping warn
    p = _write_md(tmp_path, _scene(elements))
    rep = ExcalidrawOracle().check([p])
    assert rep.verdict == "warn"
    assert any("grouping" in f for f in rep.findings)


def test_grouping_accepts_shared_frame(tmp_path):
    elements = [
        {"id": "frame1", "type": "frame",
         "x": 0, "y": 0, "width": 300, "height": 200},
        {"id": "e1", "type": "image",
         "x": 10, "y": 10, "width": 80, "height": 30,
         "frameId": "frame1",
         "customData": {"latex_source": "x^2"}},
        {"id": "e2", "type": "image",
         "x": 10, "y": 60, "width": 80, "height": 30,
         "frameId": "frame1",
         "customData": {"latex_source": "y^2"}},
    ]
    p = _write_md(tmp_path, _scene(elements))
    rep = ExcalidrawOracle().check([p])
    # No grouping warn; no rendering fail (no fileIds at all → Obsidian path)
    assert rep.verdict == "pass", rep.findings


def test_fails_when_image_fileid_has_empty_dataurl_even_with_latex_source(tmp_path):
    """2026-06-09: LaTeX rendering is now baked into write_elements (always
    materializes an SVG dataURL). The old katex escape hatch — an image+fileId
    with empty dataURL passing because customData.latex_source is present — was
    the source of the user's real broken-image boxes. Oracle now FAILS it:
    an image element with a fileId must carry a real SVG dataURL."""
    fid = "f1"
    elements = [{
        "id": "e1", "type": "image",
        "x": 0, "y": 0, "width": 100, "height": 30,
        "groupIds": ["g1"], "fileId": fid,
        "customData": {"latex_source": "x^2"},
    }]
    files = {fid: {"dataURL": ""}}
    p = _write_md(tmp_path, _scene(elements, files))
    rep = ExcalidrawOracle().check([p])
    assert rep.verdict == "fail", rep.findings
    assert any("broken-image" in f for f in rep.findings)


def test_fails_when_image_has_no_latex_source_and_empty_dataurl(tmp_path):
    """matplotlib path was clearly intended (fileId + no latex_source);
    empty dataURL means neither path is wired."""
    fid = "f1"
    elements = [{
        "id": "e1", "type": "image",
        "x": 0, "y": 0, "width": 100, "height": 30,
        "groupIds": ["g1"], "fileId": fid,
        # no customData.latex_source
    }]
    files = {fid: {"dataURL": ""}}
    p = _write_md(tmp_path, _scene(elements, files))
    rep = ExcalidrawOracle().check([p])
    assert rep.verdict == "fail"
    assert any("broken-image" in f for f in rep.findings)


def test_fails_on_orphan_fileid(tmp_path):
    elements = [{
        "id": "e1", "type": "image",
        "x": 0, "y": 0, "width": 100, "height": 30,
        "fileId": "ghost",
        "customData": {"latex_source": "x"},
    }]
    # files{} doesn't contain "ghost"
    p = _write_md(tmp_path, _scene(elements, {}))
    rep = ExcalidrawOracle().check([p])
    assert rep.verdict == "fail"
    assert any("orphan" not in f.lower() or "missing" in f for f in rep.findings)
    # explicit check that orphan finding is present
    assert any("ghost" in f for f in rep.findings)


def test_warns_on_full_overlap(tmp_path):
    # Two rectangles identically placed → 100% overlap of the smaller
    elements = [
        {"id": "r1", "type": "rectangle", "x": 0, "y": 0,
         "width": 100, "height": 100},
        {"id": "r2", "type": "rectangle", "x": 0, "y": 0,
         "width": 100, "height": 100},
    ]
    p = _write_md(tmp_path, _scene(elements))
    rep = ExcalidrawOracle().check([p])
    assert rep.verdict == "warn"
    assert any("overlap" in f for f in rep.findings)


def test_single_latex_is_not_ungrouped(tmp_path):
    elements = [{
        "id": "e1", "type": "image",
        "x": 0, "y": 0, "width": 100, "height": 30,
        "customData": {"latex_source": "x"},
    }]
    p = _write_md(tmp_path, _scene(elements))
    rep = ExcalidrawOracle().check([p])
    # 1 latex element can't be "ungrouped" — only 2+ trigger that rule
    assert rep.verdict == "pass"


def test_report_to_dict_serializable(tmp_path):
    elements = [{"id": "r1", "type": "rectangle", "x": 0, "y": 0,
                 "width": 10, "height": 10}]
    p = _write_md(tmp_path, _scene(elements))
    rep = ExcalidrawOracle().check([p])
    d = rep.to_dict()
    json.dumps(d)  # raises if not JSON-serializable
    assert d["oracle"] == "excalidraw"
