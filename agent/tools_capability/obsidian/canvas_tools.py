"""read_excalidraw_canvas + write_excalidraw_elements tool wrappers.

These are the file-level operations the agent loop calls between
"read existing canvas" and "trigger Obsidian refresh." They follow the
same _ToolBase-shaped protocol as ``refresh_note.RefreshNoteTool`` so
the factory can register them uniformly.

Design notes:
- Read returns a structured summary (frontmatter, element count, element
  type breakdown, bbox, element list) — NOT the raw 50KB JSON. That
  keeps the model context lean while still letting it reason about
  spatial layout.
- Write takes a list of element dicts to ADD (append) or REPLACE BY ID.
  The caller specifies via ``mode``. We do not implement a free-form
  "edit any field" because the existing element schema is plugin-managed
  — a partial edit is more likely to corrupt state than to fix it.
- Both tools require canvas_path to be inside vault_root; this matches
  the workspace-boundary discipline in the existing tools_v2 primitives.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.tools_capability.obsidian._mirror_guard import guard_canvas_path
from agent.tools_capability.obsidian.excalidraw_io import (
    element_bbox,
    read_canvas_file,
    write_canvas_data,
)

ElementWriteMode = Literal["append", "replace_by_id"]


@dataclass
class CanvasSummary:
    """Lean view the model sees from read_excalidraw_canvas."""

    canvas_path: str
    element_count: int
    type_breakdown: dict[str, int]
    bbox: tuple[float, float, float, float]
    elements: list[dict[str, Any]] = field(default_factory=list)
    # ID → linked file (e.g. PDF page) — handy for the model to know
    # which image elements point at which paper page.
    element_links: dict[str, str] = field(default_factory=dict)
    # fileId(sha1) → embed target, parsed from the "## Embedded Files"
    # section. This is where the Obsidian Excalidraw plugin actually
    # records PDF-page embeds (e.g. "<sha1>: [[paper.pdf#page=3]]"); the
    # image element's fileId equals the sha1 key.
    embedded_files: dict[str, str] = field(default_factory=dict)
    frontmatter_raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def read_canvas(canvas_path: Path, *, include_elements: bool = True) -> CanvasSummary:
    """Decode a .excalidraw.md and return a structured summary."""
    text = canvas_path.read_text(encoding="utf-8")
    data, _ = read_canvas_file(text)
    elements = list(data.get("elements", []))
    types: dict[str, int] = {}
    for e in elements:
        if e.get("isDeleted"):
            continue
        t = e.get("type", "?")
        types[t] = types.get(t, 0) + 1
    bbox = element_bbox(elements)

    # Pull frontmatter (lines between leading `---`)
    frontmatter = ""
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            frontmatter = text[4:end]

    # Pull a "## <heading>" section's `key: value` lines into a dict.
    def _section_kv(heading: str) -> dict[str, str]:
        out: dict[str, str] = {}
        start = text.find(heading)
        if start < 0:
            return out
        body = text[start:]
        nxt = body.find("\n## ", 1)
        body = body[: nxt if nxt > 0 else len(body)]
        for line in body.splitlines():
            if ":" not in line or line.lstrip().startswith("#"):
                continue
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if key and val:
                out[key] = val
        return out

    links = _section_kv("## Element Links")
    embedded = _section_kv("## Embedded Files")

    return CanvasSummary(
        canvas_path=str(canvas_path),
        element_count=len(elements),
        type_breakdown=types,
        bbox=bbox,
        elements=elements if include_elements else [],
        element_links=links,
        embedded_files=embedded,
        frontmatter_raw=frontmatter,
    )


@dataclass
class WriteResult:
    ok: bool
    canvas_path: str
    elements_before: int
    elements_after: int
    bytes_before: int
    bytes_after: int
    mode: ElementWriteMode
    files_added: int = 0
    error: str | None = None
    elapsed_ms: int = 0
    orphan_file_ids: list[str] = field(default_factory=list)
    viewport_focused: bool = False
    viewport: dict[str, float] | None = None
    latex_rendered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _focus_appstate_on_bbox(
    app_state: dict[str, Any],
    bbox: tuple[float, float, float, float],
    *,
    viewport_w: float = 1200.0,
    viewport_h: float = 800.0,
    fill_fraction: float = 0.6,
    zoom_min: float = 0.1,
    zoom_max: float = 2.0,
) -> dict[str, float]:
    """Mutate appState in-place so the given bbox is centered + visible.

    Excalidraw maps canvas point (x, y) to screen at
    ``((x + scrollX) * zoom, (y + scrollY) * zoom)``. To center bbox at
    the viewport center we set:
      scrollX = viewport_w / (2 * zoom) - cx
      scrollY = viewport_h / (2 * zoom) - cy
    Zoom is chosen so the bbox occupies ``fill_fraction`` of the
    viewport along its tighter axis, then clamped.
    """
    x, y, w, h = bbox
    if w <= 0:
        w = 1.0
    if h <= 0:
        h = 1.0
    cx, cy = x + w / 2.0, y + h / 2.0
    zoom = min(
        viewport_w * fill_fraction / w,
        viewport_h * fill_fraction / h,
    )
    zoom = max(zoom_min, min(zoom_max, zoom))
    scroll_x = viewport_w / (2.0 * zoom) - cx
    scroll_y = viewport_h / (2.0 * zoom) - cy

    app_state["scrollX"] = scroll_x
    app_state["scrollY"] = scroll_y
    # Excalidraw stores zoom as {value: float}; tolerate either shape.
    existing_zoom = app_state.get("zoom")
    if isinstance(existing_zoom, dict):
        existing_zoom["value"] = zoom
    else:
        app_state["zoom"] = {"value": zoom}
    return {"scrollX": scroll_x, "scrollY": scroll_y, "zoom": zoom}


def write_elements(
    *,
    canvas_path: Path,
    elements_to_write: list[dict[str, Any]],
    files_to_add: dict[str, dict[str, Any]] | None = None,
    mode: ElementWriteMode = "append",
    focus_after_write: bool = True,
) -> WriteResult:
    """Mutate a .excalidraw.md's compressed-json fence.

    ``mode='append'``: add every element in the list. New elements MUST
    each carry an ``id`` field (UUID-style is fine); ids that already
    exist in the file are rejected.

    ``mode='replace_by_id'``: each input element MUST carry an ``id``
    that matches an existing element in the file. The existing element
    is replaced wholesale. ids without a match are ignored (no implicit
    append).

    ``files_to_add``: optional dict ``{file_id: {"dataURL": "...",
    "mimeType": "...", ...}}``. Required when any element has
    ``type='image'`` and a ``fileId`` referring to data not already in
    the canvas. Without it, the L2 excalidraw oracle will report
    orphan-fileId for each image element and the canvas will show
    "image not found" placeholders. After the merge we cross-check and
    list any ``fileId`` referenced by an element-to-write that isn't in
    either ``files_to_add`` or the existing ``data['files']``.
    """
    start = time.monotonic()
    text = canvas_path.read_text(encoding="utf-8")
    bytes_before = len(text)
    data, _ = read_canvas_file(text)
    existing = data.setdefault("elements", [])
    before = len(existing)

    # Framework guarantee: any image element carrying a `latex` field (or a
    # stale customData.latex_source with no resolved dataURL) is rendered to a
    # static SVG dataURL HERE — the model can no longer ship a broken-image
    # latex element by mis-wiring fileId/SHA1 or leaving files{} empty.
    latex_notes: list[str] = []
    try:
        from agent.tools_capability.obsidian.latex_svg import (
            materialize_latex_elements,
        )
        files_to_add, latex_notes = materialize_latex_elements(
            elements_to_write, files_to_add, data.get("files") or {}
        )
    except Exception as exc:  # rendering failure must not corrupt the canvas
        return WriteResult(
            ok=False, canvas_path=str(canvas_path),
            elements_before=before, elements_after=before,
            bytes_before=bytes_before, bytes_after=bytes_before,
            mode=mode,
            error=f"LaTeX render failed: {exc}",
            elapsed_ms=_ms_since(start),
        )

    # Container-strategy guard: an element MUST NOT carry both a
    # non-empty groupIds AND a non-empty frameId. Doing so creates two
    # competing selection units in Excalidraw — clicking the frame's
    # border moves only the frame, clicking the group's content moves
    # only the group, and the user cannot scale the panel as a whole.
    # See P14.6.11 root-cause: gpt-5.5's iter2 panel A vs panel B mess.
    bad: list[str] = []
    for el in elements_to_write:
        gids = el.get("groupIds") or []
        fid = el.get("frameId")
        if gids and fid:
            bad.append(
                f"element {el.get('id', '?')!r} (type={el.get('type', '?')!r}) "
                f"has BOTH groupIds={gids!r} AND frameId={fid!r}"
            )
    if bad:
        return WriteResult(
            ok=False, canvas_path=str(canvas_path),
            elements_before=before, elements_after=before,
            bytes_before=bytes_before, bytes_after=bytes_before,
            mode=mode,
            error=(
                "container-strategy conflict: "
                + "; ".join(bad)
                + ". Pick ONE container: either group all related elements "
                "via groupIds (Ctrl+G style — click anywhere to select the "
                "group) OR wrap them in a frame via frameId (named container "
                "with its own border). Using both yields two independent "
                "selection units that cannot be scaled together."
            ),
            elapsed_ms=_ms_since(start),
        )

    existing_ids = {e.get("id") for e in existing}

    if mode == "append":
        for el in elements_to_write:
            if "id" not in el:
                return WriteResult(
                    ok=False, canvas_path=str(canvas_path),
                    elements_before=before, elements_after=before,
                    bytes_before=bytes_before, bytes_after=bytes_before,
                    mode=mode, error="every element to append must have 'id'",
                    elapsed_ms=_ms_since(start),
                )
            if el["id"] in existing_ids:
                return WriteResult(
                    ok=False, canvas_path=str(canvas_path),
                    elements_before=before, elements_after=before,
                    bytes_before=bytes_before, bytes_after=bytes_before,
                    mode=mode,
                    error=f"element id {el['id']!r} already exists; "
                          f"use mode='replace_by_id' to overwrite",
                    elapsed_ms=_ms_since(start),
                )
            existing.append(el)
    elif mode == "replace_by_id":
        index_by_id = {e.get("id"): i for i, e in enumerate(existing)}
        missing: list[str] = []
        for el in elements_to_write:
            eid = el.get("id")
            if not eid:
                missing.append("<element missing 'id' field>")
                continue
            if eid in index_by_id:
                existing[index_by_id[eid]] = el
            else:
                missing.append(str(eid))
        # Loud failure on missing IDs: silent skip was misleading. Tell
        # the model exactly which IDs are unknown and give it a slice of
        # the actual ids it can target so the next attempt is informed.
        # (P14.6.11 root-cause: DeepSeek V4-pro guessed 4 IDs blindly,
        # all missing → ok=true elements_before=elements_after=141 with
        # no observable change.)
        if missing:
            sample_ids = [
                (e.get("id") or "<noid>") for e in existing[:25]
            ]
            return WriteResult(
                ok=False, canvas_path=str(canvas_path),
                elements_before=before, elements_after=before,
                bytes_before=bytes_before, bytes_after=bytes_before,
                mode=mode,
                error=(
                    f"replace_by_id: {len(missing)} of "
                    f"{len(elements_to_write)} target ids are not in the "
                    f"canvas. Missing: {missing[:10]}"
                    + (f" (+{len(missing)-10} more)" if len(missing) > 10 else "")
                    + f". First 25 of {before} existing ids: {sample_ids}. "
                    "Read the canvas with obsidian_read_excalidraw_canvas "
                    "to discover real ids; do NOT guess ids from prior "
                    "tool results without verifying."
                ),
                elapsed_ms=_ms_since(start),
            )
    else:
        return WriteResult(
            ok=False, canvas_path=str(canvas_path),
            elements_before=before, elements_after=before,
            bytes_before=bytes_before, bytes_after=bytes_before,
            mode=mode, error=f"unsupported mode: {mode}",
            elapsed_ms=_ms_since(start),
        )

    # Merge any new files (image dataURLs). Existing entries with the
    # same key are overwritten — same as elements replace_by_id.
    files_added = 0
    if files_to_add:
        files_bucket = data.setdefault("files", {})
        for fid, fentry in files_to_add.items():
            files_bucket[fid] = fentry
            files_added += 1

    # Cross-check: any element added that references a fileId not in
    # data['files']? Surface that as orphan_file_ids so the model gets
    # an actionable signal in the tool result.
    files_seen = set((data.get("files") or {}).keys())
    referenced_fids: set[str] = set()
    for el in elements_to_write:
        if el.get("type") == "image":
            fid = el.get("fileId")
            if isinstance(fid, str):
                referenced_fids.add(fid)
    orphan = sorted(referenced_fids - files_seen)

    viewport_set: dict[str, float] | None = None
    if focus_after_write and elements_to_write:
        # element_bbox returns (min_x, min_y, max_x, max_y)
        min_x, min_y, max_x, max_y = element_bbox(elements_to_write)
        w_bb = max_x - min_x
        h_bb = max_y - min_y
        if w_bb > 0 or h_bb > 0:
            app_state = data.setdefault("appState", {})
            viewport_set = _focus_appstate_on_bbox(
                app_state, (min_x, min_y, w_bb, h_bb)
            )

    rewritten = write_canvas_data(text, data)
    canvas_path.write_text(rewritten, encoding="utf-8")
    return WriteResult(
        ok=True, canvas_path=str(canvas_path),
        elements_before=before, elements_after=len(existing),
        bytes_before=bytes_before, bytes_after=len(rewritten),
        mode=mode, files_added=files_added, orphan_file_ids=orphan,
        elapsed_ms=_ms_since(start),
        viewport_focused=viewport_set is not None,
        viewport=viewport_set,
        latex_rendered=latex_notes,
    )


def _ms_since(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


# ---------------------------------------------------------------------------
# _ToolBase-shaped wrappers
# ---------------------------------------------------------------------------


class ReadExcalidrawCanvasTool:
    @property
    def permission_level(self):  # type: ignore[no-untyped-def]
        from agent.core.loop import PermissionLevel
        return PermissionLevel.SAFE

    name = "obsidian_read_excalidraw_canvas"
    description = (
        "Read an Obsidian Excalidraw canvas (.excalidraw.md) and return its "
        "structured contents: element list, type breakdown, bounding box, "
        "element-links (id → target file mapping for PDF pages and other "
        "linked attachments), and frontmatter. Use this to inspect the "
        "current state of a canvas before deciding what to add."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "canvas_path": {
                "type": "string",
                "description": "Absolute path to the .excalidraw.md file.",
            },
            "include_elements": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If false, omit the full element list and return only "
                    "summary stats. Useful when the canvas has hundreds of "
                    "elements and the model only needs the layout outline."
                ),
            },
        },
        "required": ["canvas_path"],
    }
    parallel_safe = True

    async def run(self, input: dict, ctx) -> Any:
        from agent.core.loop import ToolResultBlock

        try:
            path = Path(input["canvas_path"]).expanduser().resolve()
        except Exception as exc:
            return ToolResultBlock(tool_use_id="",
                                   content=f"bad path: {exc}", is_error=True)
        deny = guard_canvas_path(path)
        if deny:
            return ToolResultBlock(tool_use_id="", content=deny, is_error=True)
        try:
            summary = read_canvas(
                path, include_elements=bool(input.get("include_elements", True))
            )
        except Exception as exc:
            return ToolResultBlock(tool_use_id="",
                                   content=f"read failed: {exc}", is_error=True)
        return ToolResultBlock(
            tool_use_id="",
            content=json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            is_error=False,
        )


class WriteExcalidrawElementsTool:
    @property
    def permission_level(self):  # type: ignore[no-untyped-def]
        from agent.core.loop import PermissionLevel
        # Mutates user files outside the workspace (vault) — needs approval
        # in restricted mode. In full-access mode it just runs.
        return PermissionLevel.NEEDS_APPROVAL

    name = "obsidian_write_excalidraw_elements"
    description = (
        "BATTLE-TESTED PLAYBOOK for adding content to a canvas (don't "
        "reimplement this in Python via Bash — past models that did "
        "ended up with duplicate panels, lost PDF embeds, broken "
        "lz-string fences, and orphan fileIds): "
        "(1) obsidian_read_excalidraw_canvas to learn current element "
        "IDs and the schema this canvas uses; "
        "(2) obsidian_find_pdf_text_anchor to compute the (x,y) "
        "insertion point relative to a PDF page; "
        "(3) THIS tool to write — pick ONE container strategy per "
        "panel (groupIds OR frameId, never both), and pass any image "
        "element's data via the 'files' parameter; "
        "(4) obsidian_refresh_note to surface the change in the open "
        "Obsidian view. "
        "This sequence handles lz-string round-trip, files{} consistency, "
        "schema validation, and appState viewport — all of which raw "
        "Write + Python script must reimplement and historically gets "
        "wrong.\n\n"
        "Mutate an Obsidian Excalidraw canvas (.excalidraw.md or "
        "Excalidraw-marked .md) by adding or replacing elements INSIDE its "
        "compressed-json ## Drawing fence. This is the only safe way to "
        "edit a canvas file — generic Write would overwrite the entire .md, "
        "destroying frontmatter, the plugin's lz-string fence, and every "
        "element the model didn't manually transcribe (history: a prior "
        "smoke run that used Write went 115 → 94 elements and lost the "
        "originals). This tool preserves all existing elements, lz-string "
        "round-trip safety, and the plugin schema.\n\n"
        "mode='append' adds new elements (each must carry a unique 'id'); "
        "mode='replace_by_id' overwrites existing elements matching the "
        "given ids.\n\n"
        "IMAGE ELEMENTS: when an element has type='image', its 'fileId' "
        "must point at an entry in the canvas's files{} dict. Pass that "
        "data via the 'files' parameter — without it, the element becomes "
        "an orphan reference and renders as 'image not found'. The tool's "
        "result includes 'orphan_file_ids' for any image element added "
        "whose fileId is missing from files{}; fix those before calling "
        "obsidian_refresh_note.\n\n"
        "LATEX FORMULAS (do NOT hand-wire katex): to render a math formula, "
        "add a type='image' element with a 'latex' field holding bare LaTeX "
        "(no surrounding $, e.g. \"x_i = \\\\left(x_i^{H}, "
        "f_H^{R_1}(x_i^{H})\\\\right)\") and just an x/y position — OMIT "
        "fileId, width, height, and files{}. The tool renders it to a static "
        "SVG (matplotlib mathtext), embeds the dataURL, sets fileId, and fills "
        "width/height from the formula's intrinsic size. Do NOT use "
        "customData.latex_source + a SHA1 entry in '## Embedded Files' — that "
        "katex path is fragile (fileId must equal the SHA1, which models keep "
        "getting wrong → broken-image). Optional per-element tuning: "
        "'latex_scale' (default 1.5) and 'latex_fontsize' (default 18). "
        "mathtext does NOT support \\bigl/\\Bigr — use \\left(...\\right). "
        "The result lists what was auto-rendered under 'latex_rendered'.\n\n"
        "CONTAINER STRATEGY (hard rule): an element MUST NOT carry "
        "both a non-empty groupIds AND a non-empty frameId. Excalidraw "
        "frames are NOT transparent containers — they are independent "
        "selection units. Using both yields two competing draggable "
        "shells: clicking the frame border moves only the frame, "
        "clicking grouped content moves only the group, and the panel "
        "cannot be scaled as a whole. Pick ONE strategy per panel: "
        "groupIds (Ctrl+G style, no visual border) OR frameId (named "
        "container with its own border). The tool rejects writes that "
        "violate this rule.\n\n"
        "VIEWPORT: by default (focus_after_write=true) the tool updates "
        "the canvas's appState.scrollX/scrollY/zoom so it opens centered "
        "on the newly written elements. Excalidraw's canvas is INFINITE "
        "— without this, even a perfectly-placed element is invisible "
        "because Obsidian re-opens at the saved (often zoomed-out) "
        "viewport. The result.viewport field reports {scrollX, scrollY, "
        "zoom} actually written. Set focus_after_write=false only when "
        "you're appending decoration far from the user's current view "
        "and don't want to disturb it.\n\n"
        "After writing, call obsidian_refresh_note to surface the change "
        "in the open canvas view."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "canvas_path": {
                "type": "string",
                "description": "Absolute path to the .excalidraw.md file.",
            },
            "elements": {
                "type": "array",
                "items": {"type": "object"},
                "description": (
                    "Array of Excalidraw element objects. Each MUST carry "
                    "all required fields the plugin expects (id, type, x, "
                    "y, width, height, etc.). Read an existing element via "
                    "obsidian_read_excalidraw_canvas to see the schema in "
                    "use by this vault."
                ),
            },
            "files": {
                "type": "object",
                "description": (
                    "Optional. Map of file_id -> {dataURL, mimeType, "
                    "created, ...}. Required when any new element has "
                    "type='image' so the image data is present in the "
                    "canvas's files{} dict. Standard Excalidraw entry "
                    "shape: {dataURL: 'data:image/svg+xml;base64,...', "
                    "mimeType: 'image/svg+xml', created: <ms-epoch>, "
                    "id: '<same as the map key>'}."
                ),
                "additionalProperties": {"type": "object"},
            },
            "mode": {
                "type": "string",
                "enum": ["append", "replace_by_id"],
                "default": "append",
            },
            "focus_after_write": {
                "type": "boolean",
                "default": True,
                "description": (
                    "If true (default), set the canvas's "
                    "appState.scrollX/scrollY/zoom so it opens centered "
                    "on the newly written elements at ~60% viewport "
                    "fill. Disable only when adding decoration far from "
                    "the user's current view."
                ),
            },
        },
        "required": ["canvas_path", "elements"],
    }
    parallel_safe = False

    async def run(self, input: dict, ctx) -> Any:
        from agent.core.loop import ToolResultBlock

        try:
            path = Path(input["canvas_path"]).expanduser().resolve()
        except Exception as exc:
            return ToolResultBlock(tool_use_id="",
                                   content=f"bad path: {exc}", is_error=True)
        deny = guard_canvas_path(path)
        if deny:
            return ToolResultBlock(tool_use_id="", content=deny, is_error=True)
        elements = input.get("elements") or []
        if not isinstance(elements, list):
            return ToolResultBlock(tool_use_id="",
                                   content="'elements' must be a list", is_error=True)
        files_in = input.get("files") or None
        if files_in is not None and not isinstance(files_in, dict):
            return ToolResultBlock(tool_use_id="",
                                   content="'files' must be an object", is_error=True)
        mode = input.get("mode") or "append"
        if mode not in ("append", "replace_by_id"):
            return ToolResultBlock(tool_use_id="",
                                   content=f"bad mode: {mode}", is_error=True)
        focus_raw = input.get("focus_after_write", True)
        focus_after_write = bool(focus_raw) if focus_raw is not None else True
        try:
            result = write_elements(
                canvas_path=path, elements_to_write=elements,
                files_to_add=files_in, mode=mode,
                focus_after_write=focus_after_write,
            )
        except Exception as exc:
            return ToolResultBlock(tool_use_id="",
                                   content=f"write failed: {exc}", is_error=True)
        return ToolResultBlock(
            tool_use_id="",
            content=json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            is_error=not result.ok,
        )
