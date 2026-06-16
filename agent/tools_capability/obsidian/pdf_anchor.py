"""obsidian_find_pdf_text_anchor — spatial anchor discovery for PDF-embedded canvases.

When an Excalidraw canvas embeds a PDF (one image element per page),
the canvas is infinite but the PDF occupies a small known rectangle.
The agent's blind spot is: knowing that ``(6)`` is somewhere in the PDF
does not tell it WHERE in canvas coordinates to drop a new annotation.

This tool closes the gap. Given a canvas path and a search query (e.g.
``"(6)"``), it:
1. Reads the canvas to find ``element_links`` (page-N → element_id) and
   pulls each linked image element's bbox (canvas coords).
2. Resolves the PDF path by searching the vault for the filename in the
   element_links URLs.
3. Uses pdfplumber to locate the query in the PDF, returning the char
   bbox normalized to page size.
4. Maps the normalized in-page coords onto the canvas image element's
   bbox, giving the model:
   - ``page``: which PDF page matched
   - ``page_element_id`` + ``page_bbox_canvas``: the embed location
   - ``char_bbox_canvas``: the query's location inside the embed
   - ``suggested_insert_xy``: a free spot to the right of the embed,
     vertically aligned with the match

Failure modes (returned as ``found=false``, never raise):
- Scanned-image PDFs (no embedded text) → pdfplumber finds nothing
- Query doesn't appear (typo, hyphenation, glyph encoding) → no match
- ``element_links`` empty or PDF file not in vault → cannot map
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from agent.tools_capability.obsidian._mirror_guard import guard_canvas_path
from agent.tools_capability.obsidian.canvas_tools import read_canvas

_PAGE_LINK_RE = re.compile(r"^(.*?\.pdf)#page=(\d+)$", re.IGNORECASE)


@dataclass
class AnchorMatch:
    page: int
    page_element_id: str
    page_bbox_canvas: tuple[float, float, float, float]
    char_bbox_canvas: tuple[float, float, float, float]
    char_bbox_norm: tuple[float, float, float, float]
    suggested_insert_xy: tuple[float, float]
    page_text_excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnchorResult:
    found: bool
    canvas_path: str
    pdf_path: str | None
    query: str
    matches: list[AnchorMatch] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["matches"] = [m.to_dict() if isinstance(m, AnchorMatch) else m
                         for m in self.matches]
        return d


def _find_vault_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a folder with ``.obsidian/``."""
    for parent in [start, *start.parents]:
        if (parent / ".obsidian").is_dir():
            return parent
    return None


def _resolve_pdf_path(pdf_filename: str, vault_root: Path) -> Path | None:
    """Search the vault for a PDF whose name matches the link target.

    Obsidian's wikilink resolver uses bare filenames; the file can live
    anywhere under the vault. First try the literal name, then a
    case-insensitive match.
    """
    decoded = unquote(pdf_filename)
    # Obsidian sometimes encodes spaces as underscores in URLs (the
    # vault may still have spaces on disk). Try both.
    candidates = {decoded, decoded.replace("_", " ")}
    for cand in candidates:
        direct = vault_root / cand
        if direct.is_file():
            return direct
    # Recursive search (vault may have hundreds of files; only walks once).
    targets = {c.lower() for c in candidates}
    for path in vault_root.rglob("*.pdf"):
        if path.name.lower() in targets:
            return path
    return None


def _index_canvas_pages(canvas_path: Path) -> tuple[dict[int, dict[str, Any]], str | None]:
    """Return ``{page_number: image_element_dict}`` and the linked PDF filename.

    The PDF filename is taken from the first matching link in
    ``element_links``. All links should point at the same PDF (typical
    Obsidian "embed each PDF page as an image" layout).
    """
    summary = read_canvas(canvas_path, include_elements=True)
    elements_by_id = {e["id"]: e for e in summary.elements if "id" in e}

    pages: dict[int, dict[str, Any]] = {}
    pdf_filename: str | None = None

    def _match_link(raw: str) -> tuple[str, int] | None:
        # Strip a wikilink wrapper ("[[paper.pdf#page=3]]") if present.
        s = raw.strip()
        if s.startswith("[[") and s.endswith("]]"):
            s = s[2:-2].strip()
        m = _PAGE_LINK_RE.match(s)
        return (m.group(1), int(m.group(2))) if m else None

    # Path A: "## Element Links" maps an ELEMENT id → pdf#page (explicit).
    for elem_id, link in summary.element_links.items():
        matched = _match_link(link)
        if not matched:
            continue
        fname, page_num = matched
        pdf_filename = pdf_filename or fname
        elem = elements_by_id.get(elem_id)
        # The link can point at either the page's frame (Obsidian's
        # default PDF-embed layout) or the image element itself. Both
        # have x/y/width/height we can map normalized coords into.
        if elem is None or elem.get("type") not in ("image", "frame"):
            continue
        pages[page_num] = elem

    # Path B: "## Embedded Files" maps a fileId(sha1) → pdf#page. This is
    # the layout the Obsidian Excalidraw plugin actually writes for an
    # embedded PDF; resolve each via the image element whose fileId==sha1.
    if not pages:
        elems_by_fileid: dict[str, dict[str, Any]] = {}
        for e in summary.elements:
            fid = e.get("fileId")
            if isinstance(fid, str) and e.get("type") in ("image", "frame"):
                elems_by_fileid[fid] = e
        for sha1, link in summary.embedded_files.items():
            matched = _match_link(link)
            if not matched:
                continue
            fname, page_num = matched
            pdf_filename = pdf_filename or fname
            elem = elems_by_fileid.get(sha1.strip())
            if elem is None:
                continue
            pages[page_num] = elem

    return pages, pdf_filename


def _char_match_in_page(page, query: str) -> tuple[float, float, float, float, str] | None:
    """Find the first occurrence of ``query`` in ``page.chars``.

    Returns (x0, top, x1, bottom, excerpt). Uses page.chars (not
    extract_text) so we can get bbox per character. Tolerates queries
    where consecutive chars sit on the same line.
    """
    chars = page.chars
    qlen = len(query)
    if qlen == 0 or len(chars) < qlen:
        return None
    for i in range(len(chars) - qlen + 1):
        if "".join(chars[i + k]["text"] for k in range(qlen)) == query:
            cs = chars[i:i + qlen]
            x0 = min(c["x0"] for c in cs)
            x1 = max(c["x1"] for c in cs)
            top = min(c["top"] for c in cs)
            bottom = max(c["bottom"] for c in cs)
            # Build a ~80-char excerpt around the match
            start = max(0, i - 30)
            stop = min(len(chars), i + qlen + 30)
            excerpt = "".join(c["text"] for c in chars[start:stop])
            return x0, top, x1, bottom, excerpt
    return None


def find_pdf_text_anchor(
    *,
    canvas_path: Path,
    query: str,
    max_matches: int = 5,
    insert_side: str = "right",
    gap_px: float = 20.0,
) -> AnchorResult:
    canvas_path = canvas_path.expanduser().resolve()
    if not canvas_path.is_file():
        return AnchorResult(
            found=False, canvas_path=str(canvas_path), pdf_path=None,
            query=query, error=f"canvas not found: {canvas_path}",
        )

    pages_by_num, pdf_filename = _index_canvas_pages(canvas_path)
    if not pages_by_num or pdf_filename is None:
        return AnchorResult(
            found=False, canvas_path=str(canvas_path), pdf_path=None,
            query=query,
            error=(
                "canvas has no PDF-page embeds (checked both ## Element "
                "Links and ## Embedded Files for a [[*.pdf#page=N]] link)"
            ),
        )

    vault_root = _find_vault_root(canvas_path)
    if vault_root is None:
        return AnchorResult(
            found=False, canvas_path=str(canvas_path), pdf_path=None,
            query=query, error="could not locate vault root (no .obsidian/ ancestor)",
        )

    pdf_path = _resolve_pdf_path(pdf_filename, vault_root)
    if pdf_path is None:
        return AnchorResult(
            found=False, canvas_path=str(canvas_path), pdf_path=None,
            query=query,
            error=f"PDF {pdf_filename!r} not found anywhere under vault {vault_root}",
        )

    try:
        import pdfplumber
    except ImportError as exc:
        return AnchorResult(
            found=False, canvas_path=str(canvas_path), pdf_path=str(pdf_path),
            query=query, error=f"pdfplumber not installed: {exc}",
        )

    matches: list[AnchorMatch] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_num, elem in sorted(pages_by_num.items()):
            if len(matches) >= max_matches:
                break
            if page_num < 1 or page_num > len(pdf.pages):
                continue
            page = pdf.pages[page_num - 1]
            hit = _char_match_in_page(page, query)
            if hit is None:
                continue
            x0_pt, top_pt, x1_pt, bottom_pt, excerpt = hit
            pw, ph = page.width, page.height
            nx0, ny0 = x0_pt / pw, top_pt / ph
            nx1, ny1 = x1_pt / pw, bottom_pt / ph

            ex, ey = float(elem["x"]), float(elem["y"])
            ew, eh = float(elem["width"]), float(elem["height"])

            char_canvas = (
                ex + nx0 * ew,
                ey + ny0 * eh,
                (nx1 - nx0) * ew,
                (ny1 - ny0) * eh,
            )

            if insert_side == "right":
                suggested = (ex + ew + gap_px, ey + ((ny0 + ny1) / 2) * eh)
            elif insert_side == "left":
                suggested = (ex - gap_px - 320.0, ey + ((ny0 + ny1) / 2) * eh)
            elif insert_side == "below":
                suggested = (ex + nx0 * ew, ey + eh + gap_px)
            else:
                suggested = (ex + ew + gap_px, ey + ((ny0 + ny1) / 2) * eh)

            matches.append(AnchorMatch(
                page=page_num,
                page_element_id=elem["id"],
                page_bbox_canvas=(ex, ey, ew, eh),
                char_bbox_canvas=char_canvas,
                char_bbox_norm=(nx0, ny0, nx1 - nx0, ny1 - ny0),
                suggested_insert_xy=suggested,
                page_text_excerpt=excerpt.strip(),
            ))

    return AnchorResult(
        found=bool(matches),
        canvas_path=str(canvas_path),
        pdf_path=str(pdf_path),
        query=query,
        matches=matches,
        error=None if matches else f"query {query!r} not found in any linked PDF page",
    )


# ---------------------------------------------------------------------------
# _ToolBase-shaped wrapper
# ---------------------------------------------------------------------------


class FindPdfTextAnchorTool:
    @property
    def permission_level(self):  # type: ignore[no-untyped-def]
        from agent.core.loop import PermissionLevel
        return PermissionLevel.SAFE

    name = "obsidian_find_pdf_text_anchor"
    description = (
        "Locate where a text fragment (e.g. '(6)', 'Fig. 3', 'Theorem 2') "
        "appears inside the PDFs embedded as page-images in an Obsidian "
        "Excalidraw canvas, and return the canvas-coordinate insertion "
        "point next to it. Use this BEFORE writing a new element that "
        "should sit visually near specific PDF content — the canvas is "
        "infinite, so blindly placing elements at (0,0) or the bottom "
        "leaves them invisible at the user's default zoom. Returns the "
        "matched page's bbox, the character bbox inside that page, and a "
        "suggested_insert_xy that the user will actually see when they "
        "open the canvas at the PDF embed. Returns found=false (no "
        "exception) if the PDF is image-only / the query isn't present / "
        "the canvas has no linked PDF — fall back to placing relative to "
        "an element_link bbox in that case."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "canvas_path": {
                "type": "string",
                "description": "Absolute path to the .excalidraw.md file.",
            },
            "query": {
                "type": "string",
                "description": (
                    "Exact text to search for inside the PDF. Keep it "
                    "short and distinctive ('(6)', 'Eq. 12', 'Fig. 3a'). "
                    "Matching is case-sensitive and literal — no regex."
                ),
            },
            "max_matches": {
                "type": "integer",
                "default": 5,
                "description": "Stop after this many hits across all pages.",
            },
            "insert_side": {
                "type": "string",
                "enum": ["right", "left", "below"],
                "default": "right",
                "description": (
                    "Which side of the PDF page embed to suggest the "
                    "insertion point on. 'right' is usually safe."
                ),
            },
            "gap_px": {
                "type": "number",
                "default": 20.0,
                "description": (
                    "Pixel gap between the PDF embed and the suggested "
                    "insertion point."
                ),
            },
        },
        "required": ["canvas_path", "query"],
    }
    parallel_safe = True

    async def run(self, input: dict, ctx) -> Any:
        from agent.core.loop import ToolResultBlock

        try:
            path = Path(input["canvas_path"])
        except Exception as exc:
            return ToolResultBlock(tool_use_id="",
                                   content=f"bad path: {exc}", is_error=True)
        deny = guard_canvas_path(path)
        if deny:
            return ToolResultBlock(tool_use_id="", content=deny, is_error=True)
        query = str(input.get("query") or "")
        if not query:
            return ToolResultBlock(tool_use_id="",
                                   content="'query' is required", is_error=True)
        result = find_pdf_text_anchor(
            canvas_path=path,
            query=query,
            max_matches=int(input.get("max_matches") or 5),
            insert_side=str(input.get("insert_side") or "right"),
            gap_px=float(input.get("gap_px") or 20.0),
        )
        return ToolResultBlock(
            tool_use_id="",
            content=json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            is_error=False,
        )
