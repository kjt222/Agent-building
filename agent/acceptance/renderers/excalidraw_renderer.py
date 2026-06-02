"""Excalidraw → PNG renderer for L3 vision_judge (P14.3.2).

Pillow-only layout approximation: rectangles, text, ellipses, frames, lines,
and image-element placeholders annotated with the embedded latex_source.
Not a faithful Obsidian screenshot — but enough for a vision model to judge
**layout** (are elements grouped? do formulas appear near the anchor? are
elements piled on top of each other?), which is the L3 question we ask.

Pipeline:
  1. Compute scene bbox from element coordinates
  2. Scale to fit `max_size`
  3. Draw frames first (outline + label), then shapes, then text, last
     image placeholders so latex preview text sits on top
  4. Save PNG

We deliberately do NOT try to render embedded SVG dataURLs — that requires
cairosvg / Inkscape and Windows toolchain pain; instead the placeholder
shows the latex_source so the vision judge can read it textually.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from ..excalidraw_io import load_excalidraw


_DEFAULT_MAX_SIZE = (1600, 1200)
_PADDING = 32
_BG = (255, 255, 255)
_FRAME_COLOR = (220, 53, 69)
_RECT_COLOR = (33, 37, 41)
_IMAGE_BG = (255, 248, 220)
_IMAGE_BORDER = (218, 165, 32)
_TEXT_COLOR = (0, 0, 0)


def _try_font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scene_bbox(elements: list[dict]) -> tuple[float, float, float, float] | None:
    xs1, ys1, xs2, ys2 = [], [], [], []
    for el in elements:
        x, y = el.get("x"), el.get("y")
        w, h = el.get("width"), el.get("height")
        if any(v is None for v in (x, y, w, h)):
            continue
        try:
            xs1.append(float(x))
            ys1.append(float(y))
            xs2.append(float(x) + float(w))
            ys2.append(float(y) + float(h))
        except (TypeError, ValueError):
            continue
    if not xs1:
        return None
    return min(xs1), min(ys1), max(xs2), max(ys2)


def _draw_text_in_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[float, float, float, float],
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int] = _TEXT_COLOR,
    max_lines: int = 8,
) -> None:
    if not text:
        return
    x1, y1, x2, y2 = box
    avail_w = x2 - x1 - 4
    if avail_w <= 0:
        return
    # naive word-wrap
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] > avail_w and cur:
            lines.append(cur)
            cur = w
        else:
            cur = trial
        if len(lines) >= max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    y = y1 + 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_h = bbox[3] - bbox[1] + 2
        if y + line_h > y2:
            break
        draw.text((x1 + 2, y), line, fill=fill, font=font)
        y += line_h


def render_excalidraw_scene(
    scene: dict[str, Any],
    out_path: Path,
    *,
    max_size: tuple[int, int] = _DEFAULT_MAX_SIZE,
) -> dict[str, Any]:
    """Render the scene to PNG. Returns metadata dict for summary.json."""
    elements = scene.get("elements") or []
    bbox = _scene_bbox(elements)
    if bbox is None:
        # Empty scene — emit a 1x1 blank to keep file present.
        Image.new("RGB", (64, 64), _BG).save(out_path)
        return {"rendered": True, "elements": 0, "size": [64, 64], "empty": True}

    sx1, sy1, sx2, sy2 = bbox
    scene_w = sx2 - sx1
    scene_h = sy2 - sy1
    if scene_w <= 0 or scene_h <= 0:
        Image.new("RGB", (64, 64), _BG).save(out_path)
        return {"rendered": True, "elements": len(elements), "degenerate_bbox": True}

    max_w, max_h = max_size
    scale = min((max_w - 2 * _PADDING) / scene_w, (max_h - 2 * _PADDING) / scene_h, 2.0)
    img_w = int(scene_w * scale + 2 * _PADDING)
    img_h = int(scene_h * scale + 2 * _PADDING)
    img = Image.new("RGB", (img_w, img_h), _BG)
    draw = ImageDraw.Draw(img)

    def project(x: float, y: float) -> tuple[float, float]:
        return _PADDING + (x - sx1) * scale, _PADDING + (y - sy1) * scale

    font_small = _try_font(11)
    font_med = _try_font(14)
    font_label = _try_font(12)

    # Order: frames (outline only) first, then shapes/lines, then text, then images.
    def order_key(el: dict) -> int:
        t = el.get("type", "")
        return {"frame": 0, "rectangle": 1, "ellipse": 1, "line": 2,
                "arrow": 2, "freedraw": 3, "text": 4, "image": 5}.get(t, 3)

    rendered_counts: dict[str, int] = {}

    for el in sorted(elements, key=order_key):
        et = el.get("type", "")
        rendered_counts[et] = rendered_counts.get(et, 0) + 1
        x, y = el.get("x"), el.get("y")
        w, h = el.get("width"), el.get("height")
        if any(v is None for v in (x, y, w, h)):
            continue
        try:
            x1, y1 = project(float(x), float(y))
            x2, y2 = project(float(x) + float(w), float(y) + float(h))
        except (TypeError, ValueError):
            continue
        if et == "frame":
            draw.rectangle([x1, y1, x2, y2], outline=_FRAME_COLOR, width=2)
            label = el.get("name") or "frame"
            draw.text((x1 + 4, y1 + 2), label, fill=_FRAME_COLOR, font=font_label)
        elif et == "rectangle":
            draw.rectangle([x1, y1, x2, y2], outline=_RECT_COLOR, width=1)
        elif et == "ellipse":
            draw.ellipse([x1, y1, x2, y2], outline=_RECT_COLOR, width=1)
        elif et in ("line", "arrow"):
            draw.line([x1, y1, x2, y2], fill=_RECT_COLOR, width=1)
        elif et == "text":
            text = el.get("text") or el.get("originalText") or ""
            _draw_text_in_box(draw, text, (x1, y1, x2, y2), font=font_med)
        elif et == "image":
            draw.rectangle([x1, y1, x2, y2], fill=_IMAGE_BG, outline=_IMAGE_BORDER, width=1)
            ls = (el.get("customData") or {}).get("latex_source") or ""
            label = f"[LaTeX] {ls.strip()[:200]}" if ls else "[image]"
            _draw_text_in_box(draw, label, (x1, y1, x2, y2), font=font_small,
                              fill=(101, 67, 33))

    img.save(out_path)
    return {
        "rendered": True,
        "elements": len(elements),
        "size": [img_w, img_h],
        "scene_bbox": [sx1, sy1, sx2, sy2],
        "scale": scale,
        "by_type": rendered_counts,
    }


def render_excalidraw_file(
    path: Path,
    out_path: Path,
    *,
    max_size: tuple[int, int] = _DEFAULT_MAX_SIZE,
) -> dict[str, Any]:
    """Convenience: decode then render. Returns metadata + parse status."""
    scene, err, kind = load_excalidraw(path)
    if scene is None:
        return {
            "rendered": False,
            "parse_error": err or "no excalidraw block",
            "kind": kind,
        }
    meta = render_excalidraw_scene(scene, out_path, max_size=max_size)
    meta["kind"] = kind
    meta["source_path"] = str(path)
    return meta
