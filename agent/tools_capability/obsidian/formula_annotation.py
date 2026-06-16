"""obsidian_add_formula_annotation — task-level "annotate a formula" tool.

The low-level tools (read / find_pdf_text_anchor / write_elements) each do one
thing; composing them into a correct annotation — render the LaTeX, place the
formula image, wrap an explanation below it, draw an arrow pointing at the
real formula in the PDF, and group the three so they move together — is fiddly
geometry the model historically gets wrong (text overlapping the formula,
arrow pointing nowhere, elements left ungrouped, placement off-screen on the
infinite canvas).

This tool bakes that whole recipe in. The model supplies WHAT (latex +
explanation) and WHERE (an anchor_query to locate in the embedded PDF, or an
explicit target_xy); the tool produces the three grouped, positioned,
guaranteed-renderable elements and writes them via ``write_elements`` (so the
SVG bake, files{} consistency, container-strategy guard and viewport-focus all
apply automatically).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from agent.tools_capability.obsidian._mirror_guard import guard_canvas_path
from agent.tools_capability.obsidian.canvas_tools import write_elements
from agent.tools_capability.obsidian.latex_svg import make_latex_file_entry
from agent.tools_capability.obsidian.pdf_anchor import find_pdf_text_anchor


def _now_ms() -> int:
    return int(time.time() * 1000)


def _seed() -> int:
    return uuid.uuid4().int % 2_000_000_000


def _base_fields() -> dict[str, Any]:
    """Common Excalidraw element fields so Obsidian renders without choking."""
    now = _now_ms()
    return {
        "angle": 0,
        "strokeColor": "#1e1e1e",
        "backgroundColor": "transparent",
        "fillStyle": "solid",
        "strokeWidth": 1,
        "strokeStyle": "solid",
        "roughness": 1,
        "opacity": 100,
        "frameId": None,
        "roundness": None,
        "seed": _seed(),
        "version": 1,
        "versionNonce": _seed(),
        "isDeleted": False,
        "boundElements": None,
        "updated": now,
        "link": None,
        "locked": False,
    }


# Visual-width model for wrapping mixed CJK/latin text into a fixed box.
def _char_units(ch: str) -> float:
    o = ord(ch)
    # CJK, fullwidth, kana, etc. ~ 1 em; ascii/latin ~ 0.6 em.
    if o >= 0x2E80 or 0x3000 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF:
        return 1.0
    return 0.6


def _wrap_text(text: str, max_width_px: float, font_size: int) -> tuple[str, float, float]:
    """Hard-wrap ``text`` to ``max_width_px`` and return (wrapped, w_px, h_px).

    Raw Excalidraw text elements do NOT auto-wrap, so we insert newlines
    ourselves and size the box from the resulting lines.
    """
    line_height_em = 1.25
    max_units = max(1.0, max_width_px / font_size)
    out_lines: list[str] = []
    for paragraph in text.split("\n"):
        cur = ""
        cur_units = 0.0
        for ch in paragraph:
            u = _char_units(ch)
            if cur_units + u > max_units and cur:
                out_lines.append(cur)
                cur, cur_units = ch, u
            else:
                cur += ch
                cur_units += u
        out_lines.append(cur)
    if not out_lines:
        out_lines = [""]
    widest = max(
        (sum(_char_units(c) for c in ln) for ln in out_lines), default=1.0
    )
    w_px = max(1.0, widest * font_size)
    h_px = len(out_lines) * font_size * line_height_em
    return "\n".join(out_lines), round(w_px, 1), round(h_px, 1)


@dataclass
class AnnotationResult:
    ok: bool
    canvas_path: str
    group_id: str
    element_ids: list[str] = field(default_factory=list)
    anchored_to: str | None = None          # "pdf_text" | "target_xy"
    arrow_target: tuple[float, float] | None = None
    formula_xy: tuple[float, float] | None = None
    write_result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def add_formula_annotation(
    *,
    canvas_path: Path,
    latex: str,
    explanation: str,
    anchor_query: str | None = None,
    target_xy: tuple[float, float] | None = None,
    side: str = "left",
    gap_px: float = 60.0,
    text_gap_px: float = 16.0,
    max_text_width_px: float = 360.0,
    latex_scale: float = 1.5,
    latex_fontsize: int = 18,
    text_fontsize: int = 20,
    group_id: str | None = None,
    focus_after_write: bool = True,
) -> AnnotationResult:
    """Render+place a grouped formula annotation (image + text + arrow)."""
    base = "fa_" + uuid.uuid4().hex[:8]
    gid = group_id or ("g_" + base)

    # --- 1. Resolve the formula's location on the canvas (arrow target). ---
    arrow_target: tuple[float, float] | None = None
    anchored_to: str | None = None
    page_bbox: tuple[float, float, float, float] | None = None
    if anchor_query:
        anchor = find_pdf_text_anchor(canvas_path=canvas_path, query=anchor_query)
        if anchor.found and anchor.matches:
            m = anchor.matches[0]
            cx0, cy0, cw, ch = m.char_bbox_canvas
            arrow_target = (cx0 + cw / 2.0, cy0 + ch / 2.0)
            page_bbox = m.page_bbox_canvas
            anchored_to = "pdf_text"
    if arrow_target is None and target_xy is not None:
        arrow_target = (float(target_xy[0]), float(target_xy[1]))
        anchored_to = "target_xy"
    if arrow_target is None:
        hint = (
            f"could not locate {anchor_query!r} in any embedded PDF page"
            if anchor_query
            else "no anchor_query and no target_xy given"
        )
        return AnnotationResult(
            ok=False, canvas_path=str(canvas_path), group_id=gid,
            error=(
                f"cannot place annotation: {hint}. Provide a target_xy "
                "[x, y] fallback, or call obsidian_find_pdf_text_anchor "
                "yourself to discover a placement point."
            ),
        )

    # --- 2. Pre-render the formula so we know its size for layout. ---
    try:
        fid, file_entry, fw, fh = make_latex_file_entry(
            latex, fontsize=latex_fontsize, scale=latex_scale
        )
    except Exception as exc:
        return AnnotationResult(
            ok=False, canvas_path=str(canvas_path), group_id=gid,
            error=f"LaTeX render failed: {exc}",
        )

    # --- 3. Wrap the explanation and size the whole block. ---
    wrapped, tw, th = _wrap_text(explanation, max_text_width_px, text_fontsize)
    block_w = max(fw, tw)

    # --- 4. Position the block on the chosen side of the formula. ---
    tx, ty = arrow_target
    # Reference edge: prefer the PDF page bbox so the block clears the page.
    if page_bbox is not None:
        px, py, pw, ph = page_bbox
        left_edge, right_edge = px, px + pw
    else:
        left_edge = right_edge = tx
    if side == "right":
        block_x = right_edge + gap_px
    else:  # "left" (default)
        block_x = left_edge - gap_px - block_w
    # Vertically center the block on the formula.
    block_y = ty - (fh + text_gap_px + th) / 2.0

    formula_x, formula_y = block_x, block_y
    text_x, text_y = block_x, block_y + fh + text_gap_px

    # --- 5. Build the three grouped elements. ---
    formula_el = {
        **_base_fields(),
        "id": f"{base}_formula",
        "type": "image",
        "x": round(formula_x, 1),
        "y": round(formula_y, 1),
        "width": round(fw, 1),
        "height": round(fh, 1),
        "fileId": fid,
        "scale": [1, 1],
        "status": "saved",
        "groupIds": [gid],
        "customData": {"latex_source": latex},
    }
    text_el = {
        **_base_fields(),
        "id": f"{base}_explain",
        "type": "text",
        "x": round(text_x, 1),
        "y": round(text_y, 1),
        "width": tw,
        "height": th,
        "text": wrapped,
        "originalText": wrapped,
        "fontSize": text_fontsize,
        "fontFamily": 2,
        "textAlign": "left",
        "verticalAlign": "top",
        "containerId": None,
        "lineHeight": 1.25,
        "autoResize": True,
        "groupIds": [gid],
    }
    # Arrow from the block edge facing the formula, to the formula point.
    formula_cy = formula_y + fh / 2.0
    if side == "right":
        start_x = block_x  # left edge of block points back-left to formula
    else:
        start_x = block_x + block_w  # right edge points right to formula
    start_y = formula_cy
    dx, dy = tx - start_x, ty - start_y
    arrow_el = {
        **_base_fields(),
        "id": f"{base}_arrow",
        "type": "arrow",
        "x": round(start_x, 1),
        "y": round(start_y, 1),
        "width": round(abs(dx), 1),
        "height": round(abs(dy), 1),
        "points": [[0, 0], [round(dx, 1), round(dy, 1)]],
        "lastCommittedPoint": None,
        "startBinding": None,
        "endBinding": None,
        "startArrowhead": None,
        "endArrowhead": "arrow",
        "groupIds": [gid],
    }

    # --- 6. Write via the safe path (bakes SVG, keeps files{} consistent). ---
    result = write_elements(
        canvas_path=canvas_path,
        elements_to_write=[formula_el, text_el, arrow_el],
        files_to_add={fid: file_entry},
        mode="append",
        focus_after_write=focus_after_write,
    )
    if not result.ok:
        return AnnotationResult(
            ok=False, canvas_path=str(canvas_path), group_id=gid,
            element_ids=[formula_el["id"], text_el["id"], arrow_el["id"]],
            anchored_to=anchored_to, arrow_target=arrow_target,
            formula_xy=(round(formula_x, 1), round(formula_y, 1)),
            write_result=result.to_dict(),
            error=f"write_elements failed: {result.error}",
        )
    return AnnotationResult(
        ok=True, canvas_path=str(canvas_path), group_id=gid,
        element_ids=[formula_el["id"], text_el["id"], arrow_el["id"]],
        anchored_to=anchored_to, arrow_target=arrow_target,
        formula_xy=(round(formula_x, 1), round(formula_y, 1)),
        write_result=result.to_dict(),
    )


# ---------------------------------------------------------------------------
# _ToolBase-shaped wrapper
# ---------------------------------------------------------------------------


class AddFormulaAnnotationTool:
    @property
    def permission_level(self):  # type: ignore[no-untyped-def]
        from agent.core.loop import PermissionLevel
        return PermissionLevel.NEEDS_APPROVAL

    name = "obsidian_add_formula_annotation"
    description = (
        "HIGH-LEVEL: annotate a formula on an Obsidian Excalidraw canvas in "
        "ONE call. Give the LaTeX, an explanation, and where the formula is "
        "(anchor_query to locate inside an embedded PDF, e.g. '(1)', and/or "
        "an explicit target_xy fallback). The tool renders the LaTeX to an "
        "SVG, places the formula image, wraps the explanation text below it, "
        "draws an arrow pointing at the real formula in the PDF, and groups "
        "all three so they move together — then writes them safely (SVG bake, "
        "files{} consistency, container-strategy guard, viewport focus). "
        "Prefer this over hand-building image+text+arrow elements with "
        "obsidian_write_excalidraw_elements; use the low-level tool only when "
        "you need a layout this doesn't produce. After it returns, call "
        "obsidian_refresh_note to surface the change in the open canvas. "
        "Returns the group_id, the three element ids, and the underlying "
        "write result (incl. orphan_file_ids / latex_rendered)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "canvas_path": {
                "type": "string",
                "description": "Absolute path to the .excalidraw.md file.",
            },
            "latex": {
                "type": "string",
                "description": (
                    "Bare LaTeX for the formula (no surrounding $). mathtext "
                    "does NOT support \\bigl/\\Bigr — use \\left(...\\right)."
                ),
            },
            "explanation": {
                "type": "string",
                "description": (
                    "Prose shown below the formula. Use clean text (no raw "
                    "^{} or _); it is hard-wrapped to max_text_width_px."
                ),
            },
            "anchor_query": {
                "type": "string",
                "description": (
                    "Exact, short text to locate inside the embedded PDF so "
                    "the annotation lands beside it and the arrow points at "
                    "it (e.g. '(1)', 'Eq. 12'). Case-sensitive, literal."
                ),
            },
            "target_xy": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 2,
                "maxItems": 2,
                "description": (
                    "Fallback [x, y] canvas point the arrow should point at, "
                    "used when anchor_query is absent or not found."
                ),
            },
            "side": {
                "type": "string",
                "enum": ["left", "right"],
                "default": "left",
                "description": "Which side of the formula to place the block.",
            },
            "max_text_width_px": {"type": "number", "default": 360.0},
            "latex_scale": {"type": "number", "default": 1.5},
            "latex_fontsize": {"type": "integer", "default": 18},
            "text_fontsize": {"type": "integer", "default": 20},
            "group_id": {
                "type": "string",
                "description": "Optional explicit groupId; auto-generated if omitted.",
            },
            "focus_after_write": {"type": "boolean", "default": True},
        },
        "required": ["canvas_path", "latex", "explanation"],
    }
    parallel_safe = False

    async def run(self, input: dict, ctx) -> Any:
        from agent.core.loop import ToolResultBlock
        import json

        try:
            path = Path(input["canvas_path"]).expanduser().resolve()
        except Exception as exc:
            return ToolResultBlock(tool_use_id="",
                                   content=f"bad path: {exc}", is_error=True)
        deny = guard_canvas_path(path)
        if deny:
            return ToolResultBlock(tool_use_id="", content=deny, is_error=True)
        latex = input.get("latex")
        explanation = input.get("explanation")
        if not isinstance(latex, str) or not latex.strip():
            return ToolResultBlock(tool_use_id="",
                                   content="'latex' is required", is_error=True)
        if not isinstance(explanation, str) or not explanation.strip():
            return ToolResultBlock(tool_use_id="",
                                   content="'explanation' is required", is_error=True)
        tgt = input.get("target_xy")
        target_xy = (
            (float(tgt[0]), float(tgt[1]))
            if isinstance(tgt, (list, tuple)) and len(tgt) == 2
            else None
        )
        try:
            res = add_formula_annotation(
                canvas_path=path,
                latex=latex,
                explanation=explanation,
                anchor_query=input.get("anchor_query") or None,
                target_xy=target_xy,
                side=input.get("side") or "left",
                max_text_width_px=float(input.get("max_text_width_px") or 360.0),
                latex_scale=float(input.get("latex_scale") or 1.5),
                latex_fontsize=int(input.get("latex_fontsize") or 18),
                text_fontsize=int(input.get("text_fontsize") or 20),
                group_id=input.get("group_id") or None,
                focus_after_write=bool(input.get("focus_after_write", True)),
            )
        except Exception as exc:
            return ToolResultBlock(tool_use_id="",
                                   content=f"annotation failed: {exc}", is_error=True)
        return ToolResultBlock(
            tool_use_id="",
            content=json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
            is_error=not res.ok,
        )
