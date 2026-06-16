"""LaTeX → self-contained SVG dataURL, baked into the obsidian write tool.

Why this lives in the framework (not left to the model):
    Round 10/11/… smoke runs and the 2026-06-05 doubao-seed-2-0-pro run all
    proved the same failure mode — a model writes ``customData.latex_source``
    and *believes* Obsidian's katex will render it, but the image element's
    ``fileId`` does not equal the latex SHA1 (and ``files{}`` has no dataURL),
    so the canvas shows a broken-image placeholder. The katex path depends on
    a fragile fileId==SHA1 linkage the model keeps getting wrong.

    The robust path is a *static* matplotlib-mathtext SVG embedded as a base64
    dataURL on the element's ``fileId`` — it renders regardless of plugin katex
    support. By doing the render INSIDE ``write_elements`` we make the broken
    state unreachable: the model only supplies the LaTeX string (+ position),
    and the tool guarantees ``files[fileId].dataURL`` is a real rendered SVG.

Recipe mirrors skills/obsidian-excalidraw/SKILL.md (render → strip root
width/height keep viewBox → base64). matplotlib is imported lazily so the
module costs nothing until an actual LaTeX element is written; a missing
matplotlib surfaces as an actionable error rather than an import crash.
"""

from __future__ import annotations

import base64
import io
import re
import time
import uuid
from typing import Any

_PT_TO_PX = 1.333  # Excalidraw uses CSS px; matplotlib SVG viewBox is in pt.


class LatexRenderError(RuntimeError):
    """Raised when a LaTeX source cannot be rendered to SVG."""


def render_latex_to_svg(latex_source: str, fontsize: int = 18) -> bytes:
    """Render bare LaTeX (no surrounding ``$``) to a self-contained,
    container-scalable SVG byte string.

    The root ``<svg>``'s explicit ``width``/``height`` are stripped (viewBox
    kept) so Excalidraw resize handles scale the *formula*, not just the box.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless; no Tk on the server box
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover - env-dependent
        raise LatexRenderError(
            "matplotlib is required to render LaTeX image elements but could "
            f"not be imported ({exc!r}). Install it in the agent venv "
            "(pip install matplotlib)."
        ) from exc

    fig = plt.figure(figsize=(0.01, 0.01))
    try:
        fig.text(0.0, 0.0, f"${latex_source}$", ha="left", va="bottom",
                 fontsize=fontsize)
        buf = io.BytesIO()
        fig.savefig(buf, format="svg", bbox_inches="tight", pad_inches=0.05,
                    transparent=True)
    except Exception as exc:
        raise LatexRenderError(
            f"matplotlib mathtext failed to parse LaTeX {latex_source!r}: "
            f"{exc}. Note mathtext does NOT support \\bigl/\\Bigr — use "
            r"\left(...\right)."
        ) from exc
    finally:
        plt.close(fig)

    svg = buf.getvalue().decode("utf-8")
    svg = re.sub(r'(<svg[^>]*?)\s+width="[^"]+"', r"\1", svg, count=1)
    svg = re.sub(r'(<svg[^>]*?)\s+height="[^"]+"', r"\1", svg, count=1)
    return svg.encode("utf-8")


def svg_pixel_size(svg_bytes: bytes) -> tuple[float, float]:
    """Read the SVG's intrinsic size (pt) from its viewBox and convert to px."""
    head = svg_bytes[:512].decode("ascii", errors="ignore")
    m = re.search(
        r'viewBox="\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"', head
    )
    if m is None:
        raise LatexRenderError(f"rendered SVG missing viewBox: {head[:200]!r}")
    return float(m.group(1)) * _PT_TO_PX, float(m.group(2)) * _PT_TO_PX


def make_latex_file_entry(latex_source: str, *, fontsize: int = 18,
                          scale: float = 1.5) -> tuple[str, dict[str, Any],
                                                       float, float]:
    """Render ``latex_source`` and build the Excalidraw files{} entry for it.

    Returns ``(file_id, file_entry, width_px, height_px)``. ``width_px`` /
    ``height_px`` already include ``scale`` and are ready to drop onto the
    image element. The ``file_id`` is the key the element's ``fileId`` must use.
    """
    svg_bytes = render_latex_to_svg(latex_source, fontsize=fontsize)
    w_px, h_px = svg_pixel_size(svg_bytes)
    fid = "lf_" + uuid.uuid4().hex[:12]
    now_ms = int(time.time() * 1000)
    entry = {
        "id": fid,
        "mimeType": "image/svg+xml",
        "dataURL": "data:image/svg+xml;base64,"
                   + base64.b64encode(svg_bytes).decode("ascii"),
        "created": now_ms,
        "lastRetrieved": now_ms,
    }
    return fid, entry, w_px * scale, h_px * scale


def materialize_latex_elements(
    elements: list[dict[str, Any]],
    files_to_add: dict[str, dict[str, Any]] | None,
    existing_files: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Mutate ``elements`` in place so every LaTeX image is guaranteed to
    render, and return the (possibly newly-created) ``files_to_add`` plus a
    list of human-readable notes about what was auto-rendered.

    An element opts into auto-render by being ``type='image'`` and carrying a
    convenience ``latex`` field (bare LaTeX, no ``$``). We also rescue elements
    that only set ``customData.latex_source`` but whose ``fileId`` has no
    dataURL anywhere — exactly the historical broken-image state.

    For each such element we render an SVG, register it in ``files_to_add``
    under a fresh fileId, point the element's ``fileId`` at it, fill missing
    width/height from the SVG's intrinsic size, and stamp
    ``customData.latex_source`` so the source stays re-editable.
    """
    out_files: dict[str, dict[str, Any]] = dict(files_to_add or {})
    notes: list[str] = []
    have_data = set(existing_files.keys()) | set(out_files.keys())

    for el in elements:
        if el.get("type") != "image":
            continue
        # Source of truth for the latex string, in priority order.
        latex = el.pop("latex", None)
        cdata = el.get("customData") or {}
        if not latex:
            latex = cdata.get("latex_source")
        if not latex or not isinstance(latex, str):
            continue

        fid = el.get("fileId")
        # Already wired to real image data? leave it alone.
        if isinstance(fid, str) and fid in have_data:
            url = (out_files.get(fid) or existing_files.get(fid) or {}).get("dataURL")
            if url:
                continue

        scale = float(el.pop("latex_scale", 1.5) or 1.5)
        fontsize = int(el.pop("latex_fontsize", 18) or 18)
        new_fid, entry, w_px, h_px = make_latex_file_entry(
            latex, fontsize=fontsize, scale=scale
        )
        el["fileId"] = new_fid
        out_files[new_fid] = entry
        have_data.add(new_fid)
        # Fill geometry only if the model didn't give sensible numbers.
        if not isinstance(el.get("width"), (int, float)) or not el.get("width"):
            el["width"] = round(w_px, 1)
        if not isinstance(el.get("height"), (int, float)) or not el.get("height"):
            el["height"] = round(h_px, 1)
        # Keep source for re-editability; clear any stale katex linkage hint.
        el["customData"] = {"latex_source": latex}
        el.setdefault("scale", [1, 1])
        el.setdefault("status", "saved")
        notes.append(
            f"auto-rendered LaTeX element {el.get('id', '?')!r} → SVG "
            f"({len(entry['dataURL'])}B dataURL, fileId={new_fid})"
        )

    return out_files, notes
