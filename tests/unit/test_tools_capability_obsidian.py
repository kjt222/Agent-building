"""Unit tests for agent/tools_capability/obsidian/* (P14.6.4).

These tests do NOT hit a real Obsidian instance — the REST client is
faked via a stand-in object whose methods record the call sequence so we
can assert refresh_note really fires open → close → open in that order.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.tools_capability.obsidian.excalidraw_io import (
    decode_fence,
    element_bbox,
    encode_fence,
    read_canvas_file,
    write_canvas_data,
)
from agent.tools_capability.obsidian.canvas_tools import (
    read_canvas,
    write_elements,
)
from agent.tools_capability.obsidian.refresh_note import (
    RefreshResult,
    refresh_note_sync,
)


# ---------------------------------------------------------------------------
# Excalidraw IO helpers
# ---------------------------------------------------------------------------


def _build_canvas_file(elements: list[dict]) -> str:
    """Synthesize a minimal .excalidraw.md file content."""
    payload = {
        "type": "excalidraw",
        "version": 2,
        "source": "test",
        "elements": elements,
        "appState": {},
        "files": {},
    }
    fence = encode_fence(payload)
    return (
        "---\nexcalidraw-plugin: parsed\ntags: [excalidraw]\n---\n"
        "# Excalidraw Data\n## Text Elements\n\n## Drawing\n"
        f"```compressed-json\n{fence}\n```\n%%\n"
    )


def test_encode_decode_roundtrip_preserves_elements():
    elements = [{"id": "a", "type": "text", "x": 10, "y": 20,
                 "width": 50, "height": 30, "text": "hi"}]
    fence = encode_fence({"elements": elements, "appState": {}})
    decoded = decode_fence(fence)
    assert decoded["elements"] == elements


def test_decode_fence_tolerates_embedded_newlines():
    """The Excalidraw plugin wraps fence content at ~80 chars; our
    decoder must strip whitespace before passing to lz-string."""
    elements = [{"id": "a", "type": "text", "x": 0, "y": 0,
                 "width": 100, "height": 20, "text": "abc"}]
    fence = encode_fence({"elements": elements})
    # Inject newlines every 30 chars to mimic plugin's wrapping.
    wrapped = "\n".join(fence[i:i + 30] for i in range(0, len(fence), 30))
    decoded = decode_fence(wrapped)
    assert decoded["elements"] == elements


def test_decode_fence_raises_on_empty_input():
    with pytest.raises(ValueError):
        decode_fence("not_real_base64____")


def test_read_canvas_file_returns_decoded_dict_and_fence_span():
    elements = [{"id": "x", "type": "rectangle", "x": 1, "y": 2,
                 "width": 3, "height": 4}]
    text = _build_canvas_file(elements)
    data, (start, end) = read_canvas_file(text)
    assert data["elements"] == elements
    # Verify the span really points at the fence body (not the backticks)
    assert "compressed-json" not in text[start:end]
    assert "```" not in text[start:end]


def test_read_canvas_file_raises_if_no_fence():
    with pytest.raises(ValueError):
        read_canvas_file("---\njust frontmatter\n---\nno fence here")


def test_write_canvas_data_replaces_only_the_fence_body():
    """Frontmatter and outside-fence content must be byte-preserved."""
    original = _build_canvas_file([{"id": "a", "type": "text", "x": 0, "y": 0,
                                    "width": 10, "height": 10, "text": "old"}])
    data, _ = read_canvas_file(original)
    data["elements"].append({"id": "b", "type": "text", "x": 5, "y": 5,
                              "width": 10, "height": 10, "text": "new"})
    rewritten = write_canvas_data(original, data)

    # Frontmatter preserved
    assert rewritten.startswith("---\nexcalidraw-plugin: parsed\n")
    # Trailing %% preserved
    assert rewritten.rstrip().endswith("%%")
    # And the new element is reachable
    new_data, _ = read_canvas_file(rewritten)
    assert {e["text"] for e in new_data["elements"]} == {"old", "new"}


def test_element_bbox_skips_deleted_and_no_coord_elements():
    elements = [
        {"x": 0, "y": 0, "width": 10, "height": 10},
        {"x": 100, "y": 200, "width": 50, "height": 50},
        {"isDeleted": True, "x": -999, "y": -999, "width": 1, "height": 1},
        {"x": None, "y": None},  # ill-formed
    ]
    bbox = element_bbox(elements)
    assert bbox == (0.0, 0.0, 150.0, 250.0)


def test_element_bbox_returns_zeros_on_empty_input():
    assert element_bbox([]) == (0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# refresh_note_sync (with stub client)
# ---------------------------------------------------------------------------


class _StubClient:
    """Records every call so we can assert ordering."""

    def __init__(self, *, active_size: int | None = 47097):
        self.calls: list[tuple[str, str]] = []
        self.active_size = active_size

    def open_file(self, rel: str) -> None:
        self.calls.append(("open", rel))

    def execute_command(self, cmd: str) -> None:
        self.calls.append(("cmd", cmd))

    def active_note(self) -> dict:
        return {"stat": {"size": self.active_size}} if self.active_size is not None else {}


def test_refresh_note_fires_open_close_open_in_order(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    canvas = vault / "sub" / "note.md"
    canvas.parent.mkdir()
    canvas.write_text("x", encoding="utf-8")

    stub = _StubClient()
    result = refresh_note_sync(
        vault_root=vault, canvas_path=canvas, client=stub,
        open_delay_s=0.0, close_delay_s=0.0, render_delay_s=0.0,
    )
    assert result.ok is True
    # exact ordering matters — open, close, open
    assert stub.calls == [
        ("open", "sub/note.md"),
        ("cmd", "workspace:close"),
        ("open", "sub/note.md"),
    ]
    assert result.active_size_after_ms == 47097


def test_refresh_note_rejects_path_outside_vault(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "other" / "note.md"
    outside.parent.mkdir()
    outside.write_text("x", encoding="utf-8")

    stub = _StubClient()
    result = refresh_note_sync(
        vault_root=vault, canvas_path=outside, client=stub,
        open_delay_s=0.0, close_delay_s=0.0, render_delay_s=0.0,
    )
    assert result.ok is False
    assert "not under vault" in (result.error or "")
    # No REST calls fired
    assert stub.calls == []


def test_refresh_note_returns_failure_when_open1_raises(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    canvas = vault / "note.md"
    canvas.write_text("x", encoding="utf-8")

    class _BrokenClient(_StubClient):
        def open_file(self, rel):
            raise RuntimeError("plugin offline")

    result = refresh_note_sync(
        vault_root=vault, canvas_path=canvas, client=_BrokenClient(),
        open_delay_s=0.0, close_delay_s=0.0, render_delay_s=0.0,
    )
    assert result.ok is False
    assert "open1 failed" in (result.error or "")


def test_refresh_note_reports_active_size_even_when_0(tmp_path):
    """Critical signal: model needs to see the broken-buffer state."""
    vault = tmp_path / "vault"
    vault.mkdir()
    canvas = vault / "note.md"
    canvas.write_text("x", encoding="utf-8")

    stub = _StubClient(active_size=0)
    result = refresh_note_sync(
        vault_root=vault, canvas_path=canvas, client=stub,
        open_delay_s=0.0, close_delay_s=0.0, render_delay_s=0.0,
    )
    # OK=True because REST calls themselves succeeded — but size=0
    # tells the model the canvas is in the empty-buffer trap state.
    assert result.ok is True
    assert result.active_size_after_ms == 0


def test_refresh_result_to_dict_is_json_serializable():
    r = RefreshResult(ok=True, elapsed_ms=42, open1_status=200,
                       close_status=204, open2_status=200,
                       active_size_after_ms=10000)
    encoded = json.dumps(r.to_dict())
    decoded = json.loads(encoded)
    assert decoded["ok"] is True
    assert decoded["active_size_after_ms"] == 10000


# ---------------------------------------------------------------------------
# canvas_tools.read_canvas + write_elements
# ---------------------------------------------------------------------------


def test_read_canvas_returns_type_breakdown_and_bbox(tmp_path):
    text = _build_canvas_file([
        {"id": "a", "type": "text", "x": 0, "y": 0,
         "width": 100, "height": 30, "text": "hi"},
        {"id": "b", "type": "image", "x": -50, "y": 200,
         "width": 400, "height": 300, "fileId": "img1"},
        {"id": "c", "type": "image", "x": 50, "y": 800,
         "width": 200, "height": 200, "fileId": "img2"},
    ])
    f = tmp_path / "canvas.md"
    f.write_text(text, encoding="utf-8")

    summary = read_canvas(f)
    assert summary.element_count == 3
    assert summary.type_breakdown == {"text": 1, "image": 2}
    assert summary.bbox == (-50.0, 0.0, 350.0, 1000.0)
    assert len(summary.elements) == 3


def test_read_canvas_can_omit_elements_list(tmp_path):
    text = _build_canvas_file([
        {"id": str(i), "type": "rectangle", "x": i, "y": i,
         "width": 10, "height": 10}
        for i in range(100)
    ])
    f = tmp_path / "canvas.md"
    f.write_text(text, encoding="utf-8")
    summary = read_canvas(f, include_elements=False)
    assert summary.element_count == 100
    assert summary.elements == []
    # Stats still populated
    assert summary.type_breakdown == {"rectangle": 100}


def test_read_canvas_parses_element_links_section(tmp_path):
    text = (
        "---\nexcalidraw-plugin: parsed\n---\n# Excalidraw Data\n"
        "## Text Elements\n\n## Element Links\n"
        "abc123: paper.pdf#page=5\n"
        "def456: paper.pdf#page=6\n"
        "\n## Drawing\n```compressed-json\n"
        f"{encode_fence({'elements': []})}\n```\n"
    )
    f = tmp_path / "canvas.md"
    f.write_text(text, encoding="utf-8")
    summary = read_canvas(f)
    assert summary.element_links == {
        "abc123": "paper.pdf#page=5",
        "def456": "paper.pdf#page=6",
    }


def test_write_elements_append_adds_to_existing(tmp_path):
    initial = [{"id": "a", "type": "text", "x": 0, "y": 0,
                "width": 10, "height": 10, "text": "old"}]
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file(initial), encoding="utf-8")

    new_elem = {"id": "b", "type": "text", "x": 50, "y": 50,
                "width": 10, "height": 10, "text": "new"}
    r = write_elements(canvas_path=f, elements_to_write=[new_elem])
    assert r.ok is True
    assert r.elements_before == 1
    assert r.elements_after == 2
    assert r.mode == "append"

    # Confirm file actually contains both elements now
    summary = read_canvas(f)
    assert {e["text"] for e in summary.elements} == {"old", "new"}


def test_write_elements_append_rejects_missing_id(tmp_path):
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    r = write_elements(
        canvas_path=f,
        elements_to_write=[{"type": "text", "x": 0, "y": 0,
                            "width": 10, "height": 10, "text": "x"}],
    )
    assert r.ok is False
    assert "must have 'id'" in (r.error or "")
    assert r.elements_after == 0  # nothing appended


def test_write_elements_append_rejects_duplicate_id(tmp_path):
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([
        {"id": "a", "type": "text", "x": 0, "y": 0,
         "width": 10, "height": 10, "text": "x"}
    ]), encoding="utf-8")
    r = write_elements(
        canvas_path=f,
        elements_to_write=[{"id": "a", "type": "text", "x": 5, "y": 5,
                            "width": 10, "height": 10, "text": "duplicate"}],
    )
    assert r.ok is False
    assert "already exists" in (r.error or "")


def test_write_elements_replace_by_id_overwrites(tmp_path):
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([
        {"id": "a", "type": "text", "x": 0, "y": 0,
         "width": 10, "height": 10, "text": "before"}
    ]), encoding="utf-8")
    r = write_elements(
        canvas_path=f,
        elements_to_write=[{"id": "a", "type": "text", "x": 0, "y": 0,
                            "width": 10, "height": 10, "text": "after"}],
        mode="replace_by_id",
    )
    assert r.ok is True
    assert r.elements_after == 1  # still 1 element (replaced, not added)
    summary = read_canvas(f)
    assert summary.elements[0]["text"] == "after"


def test_write_elements_replace_by_id_errors_on_missing_id(tmp_path):
    """replace_by_id must NOT silent-skip missing ids: that misleads
    the model into thinking ok=true means its write took effect.
    See P14.6.12 root-cause: DeepSeek V4-pro hallucinated ids and
    got ok=true with elements_before == elements_after."""
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    r = write_elements(
        canvas_path=f,
        elements_to_write=[{"id": "doesnt-exist", "type": "text",
                            "x": 0, "y": 0, "width": 10, "height": 10,
                            "text": "ghost"}],
        mode="replace_by_id",
    )
    assert r.ok is False
    assert "doesnt-exist" in (r.error or "")
    assert r.elements_after == 0  # file untouched


def test_write_elements_attaches_files_dict(tmp_path):
    """Image elements need their dataURL in files{} or they render as
    'image not found'. The tool must accept and merge files_to_add."""
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    img_element = {
        "id": "img1", "type": "image", "x": 0, "y": 0,
        "width": 100, "height": 100, "fileId": "lf_abc",
    }
    files = {
        "lf_abc": {
            "dataURL": "data:image/svg+xml;base64,PHN2Zy8+",
            "mimeType": "image/svg+xml",
            "id": "lf_abc",
            "created": 1000,
        },
    }
    r = write_elements(
        canvas_path=f, elements_to_write=[img_element],
        files_to_add=files,
    )
    assert r.ok is True
    assert r.files_added == 1
    assert r.orphan_file_ids == []  # fileId matched
    # Round-trip — the dataURL survives lz-string encode/decode
    summary = read_canvas(f)
    elements = summary.elements
    assert elements[0]["fileId"] == "lf_abc"


def test_write_elements_surfaces_orphan_file_ids(tmp_path):
    """When the model adds an image element but forgets to pass its
    file data, the tool must say so loud and clear in the result."""
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    img_element = {
        "id": "img1", "type": "image", "x": 0, "y": 0,
        "width": 100, "height": 100, "fileId": "lf_missing",
    }
    r = write_elements(
        canvas_path=f, elements_to_write=[img_element],
        files_to_add=None,  # forgot
    )
    assert r.ok is True  # the write itself succeeded
    assert r.orphan_file_ids == ["lf_missing"]
    assert r.files_added == 0


def test_write_elements_files_merge_preserves_existing(tmp_path):
    """If the canvas already has files in its data['files'], adding new
    ones must not wipe them."""
    # Inject existing files into the baseline canvas.
    payload = {"elements": [], "files": {"old_fid": {"dataURL": "data:x"}}}
    fence = encode_fence(payload)
    raw = (
        "---\nexcalidraw-plugin: parsed\n---\n# Excalidraw Data\n"
        "## Text Elements\n\n## Drawing\n"
        f"```compressed-json\n{fence}\n```\n%%\n"
    )
    f = tmp_path / "canvas.md"
    f.write_text(raw, encoding="utf-8")
    r = write_elements(
        canvas_path=f,
        elements_to_write=[],
        files_to_add={"new_fid": {"dataURL": "data:y"}},
    )
    assert r.ok and r.files_added == 1
    # Re-decode and confirm both files survive
    text = f.read_text(encoding="utf-8")
    from agent.tools_capability.obsidian.excalidraw_io import read_canvas_file
    data, _ = read_canvas_file(text)
    assert set(data["files"].keys()) == {"old_fid", "new_fid"}


def test_write_elements_rejects_unknown_mode(tmp_path):
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    r = write_elements(
        canvas_path=f,
        elements_to_write=[],
        mode="upsert",  # type: ignore[arg-type]
    )
    assert r.ok is False
    assert "unsupported mode" in (r.error or "")


def test_write_elements_focus_after_write_centers_appstate_on_bbox(tmp_path):
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    new_elem = {
        "id": "focus-target", "type": "text",
        "x": 1000.0, "y": -3500.0, "width": 200.0, "height": 60.0,
        "text": "公式 (6)",
    }
    r = write_elements(canvas_path=f, elements_to_write=[new_elem])
    assert r.ok is True
    assert r.viewport_focused is True
    assert r.viewport is not None
    # Re-read; appState should now contain scrollX/Y/zoom focused on bbox center
    from agent.tools_capability.obsidian.excalidraw_io import read_canvas_file
    text = f.read_text(encoding="utf-8")
    data, _ = read_canvas_file(text)
    app = data.get("appState") or {}
    assert "scrollX" in app and "scrollY" in app
    zoom = app["zoom"]["value"] if isinstance(app.get("zoom"), dict) else app["zoom"]
    assert 0.1 <= zoom <= 2.0
    # Element center is (1100, -3470). At viewport 1200×800:
    # viewport center is (-scrollX + W/(2*zoom), -scrollY + H/(2*zoom))
    # → solving back: -scrollX = 1100 - W/(2*zoom)
    expected_scroll_x = 1200.0 / (2.0 * zoom) - 1100.0
    expected_scroll_y = 800.0 / (2.0 * zoom) - (-3470.0)
    assert abs(app["scrollX"] - expected_scroll_x) < 1e-3
    assert abs(app["scrollY"] - expected_scroll_y) < 1e-3


def test_write_elements_replace_by_id_surfaces_missing_ids(tmp_path):
    initial = [{"id": "alpha", "type": "text", "x": 0, "y": 0,
                "width": 10, "height": 10, "text": "hi"}]
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file(initial), encoding="utf-8")

    bogus = {"id": "doesnotexist", "type": "text", "x": 0, "y": 0,
             "width": 10, "height": 10, "text": "x"}
    r = write_elements(
        canvas_path=f,
        elements_to_write=[bogus],
        mode="replace_by_id",
    )
    assert r.ok is False
    err = r.error or ""
    assert "replace_by_id" in err
    assert "doesnotexist" in err
    assert "alpha" in err  # surfaces real ids the model can use
    # File untouched
    summary = read_canvas(f)
    assert summary.element_count == 1


def test_write_elements_rejects_frame_and_group_dual_strategy(tmp_path):
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    bad = {
        "id": "bad", "type": "rectangle", "x": 0, "y": 0,
        "width": 10, "height": 10,
        "groupIds": ["grp_abc"],
        "frameId": "frame_xyz",
    }
    r = write_elements(canvas_path=f, elements_to_write=[bad])
    assert r.ok is False
    err = r.error or ""
    assert "container-strategy conflict" in err
    assert "groupIds" in err and "frameId" in err
    # File untouched
    from agent.tools_capability.obsidian.excalidraw_io import read_canvas_file
    data, _ = read_canvas_file(f.read_text(encoding="utf-8"))
    assert data.get("elements", []) == []


def test_write_elements_focus_after_write_disabled_leaves_appstate_alone(tmp_path):
    f = tmp_path / "canvas.md"
    f.write_text(_build_canvas_file([]), encoding="utf-8")
    new_elem = {"id": "x", "type": "text", "x": 5000, "y": 5000,
                "width": 50, "height": 50, "text": "off-screen"}
    r = write_elements(
        canvas_path=f, elements_to_write=[new_elem],
        focus_after_write=False,
    )
    assert r.ok is True
    assert r.viewport_focused is False
    assert r.viewport is None
    from agent.tools_capability.obsidian.excalidraw_io import read_canvas_file
    data, _ = read_canvas_file(f.read_text(encoding="utf-8"))
    app = data.get("appState") or {}
    # Builder didn't set these; tool shouldn't either
    assert "scrollX" not in app
    assert "scrollY" not in app
