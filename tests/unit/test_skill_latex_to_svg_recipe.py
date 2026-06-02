"""Tests for the LaTeX → SVG → files[].dataURL recipe documented in
``skills/obsidian-excalidraw/SKILL.md`` (#P13.2.3).

Round 10 / 11 smoke runs proved that models can write
``customData.latex_source`` but skip the SVG rendering step, leaving
``files{}`` empty so Obsidian renders a broken-image placeholder. The
SKILL.md fix teaches matplotlib mathtext → SVG → base64. These tests:

1. The matplotlib recipe in SKILL.md actually produces a non-trivial,
   self-contained SVG that decodes round-trip via the documented helper.
2. SKILL.md must mention matplotlib, mathtext, ``bbox_inches='tight'``,
   ``transparent=True``, and the dataURL base64 prefix.
3. SKILL.md must warn against using ``katex`` / system TeX / forgetting
   ``matplotlib.use('Agg')`` / forgetting base64 encoding.
4. A FileVerify python_predicate template must enforce that every
   ``latex_source`` image has a non-empty ``files[fileId].dataURL``.
"""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


SKILL = Path(__file__).resolve().parents[2] / "skills" / "obsidian-excalidraw" / "SKILL.md"


def _render_latex_to_svg(latex_source: str, fontsize: int = 18) -> bytes:
    """Mirrors the SKILL.md recipe: render → strip root width/height,
    keep viewBox so Excalidraw resize handles actually scale the formula."""
    import re
    fig = plt.figure(figsize=(0.01, 0.01))
    fig.text(0.0, 0.0, f"${latex_source}$",
             ha="left", va="bottom", fontsize=fontsize)
    buf = io.BytesIO()
    fig.savefig(buf, format="svg",
                bbox_inches="tight",
                pad_inches=0.05,
                transparent=True)
    plt.close(fig)
    svg = buf.getvalue().decode("utf-8")
    svg = re.sub(r'(<svg[^>]*?)\s+width="[^"]+"', r"\1", svg, count=1)
    svg = re.sub(r'(<svg[^>]*?)\s+height="[^"]+"', r"\1", svg, count=1)
    return svg.encode("utf-8")


def test_matplotlib_renders_tlm_formula_to_svg() -> None:
    """Real TLM-style formula renders to non-trivial self-contained SVG."""
    svg = _render_latex_to_svg(
        r"\rho_c = R_{sh,s}\,L_t^{2},\quad L_t = \sqrt{\rho_c / R_{sh,s}}"
    )
    assert svg.startswith(b"<?xml"), "SVG should start with XML declaration"
    assert b"<svg" in svg
    assert b"</svg>" in svg
    assert len(svg) > 2000, f"SVG suspiciously small: {len(svg)}B"


def test_svg_base64_dataurl_decodes_round_trip() -> None:
    """SVG → base64 → dataURL → decode round-trip works without corruption."""
    svg = _render_latex_to_svg(r"V(x) = I\,R_{sh,s}\,\cosh\left((L-x)/L_t\right)")
    b64 = base64.b64encode(svg).decode("ascii")
    data_url = f"data:image/svg+xml;base64,{b64}"
    assert data_url.startswith("data:image/svg+xml;base64,")
    decoded = base64.b64decode(data_url.split(",", 1)[1])
    assert decoded == svg


def test_svg_root_has_viewbox_but_no_explicit_pt_size() -> None:
    """For Excalidraw resize handles to scale the formula (not just the
    bounding box), the SVG root MUST keep ``viewBox`` but MUST NOT
    declare explicit ``width=...pt`` / ``height=...pt`` — those make the
    SVG self-size and ignore the container."""
    svg = _render_latex_to_svg(r"\alpha + \beta = \gamma")
    head = svg[:512].decode("ascii", errors="ignore")
    svg_tag = re.search(r"<svg[^>]*>", head)
    assert svg_tag, f"no <svg> open tag in head: {head!r}"
    tag = svg_tag.group(0)
    assert re.search(r'viewBox="\s*[\d.\-]+\s+[\d.\-]+\s+[\d.]+\s+[\d.]+"', tag), \
        f"SVG root missing viewBox: {tag!r}"
    assert not re.search(r'\swidth="[^"]+"', tag), \
        f"SVG root must not declare explicit width — Excalidraw resize won't scale content. tag={tag!r}"
    assert not re.search(r'\sheight="[^"]+"', tag), \
        f"SVG root must not declare explicit height — Excalidraw resize won't scale content. tag={tag!r}"


def test_skill_md_teaches_matplotlib_mathtext_recipe() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for needle in [
        "matplotlib",
        "mathtext",
        "bbox_inches",
        "transparent=True",
        'matplotlib.use("Agg")',
        "data:image/svg+xml;base64,",
        "render_latex_to_svg",
        "insert_latex_image",
        "viewBox",          # scalable SVG: must keep viewBox
    ]:
        assert needle in text, f"SKILL.md missing required snippet: {needle!r}"


def test_skill_md_warns_about_excalidraw_resize_scaling_quirk() -> None:
    """SKILL.md must mention that root width/height get stripped so
    Excalidraw resize handles scale the formula content (not just the
    bounding box). This is the failure mode the user hit on 2026-05-19."""
    text = SKILL.read_text(encoding="utf-8")
    # Must teach the strip step explicitly
    assert "width=" in text and "height=" in text
    assert "viewBox" in text
    # And explain the why
    assert "缩放" in text or "scale" in text.lower()


def test_skill_md_warns_against_known_failure_modes() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for warn in [
        "katex",        # don't use npx katex
        "usetex",       # don't enable usetex (calls system TeX)
        "Agg",          # must set non-interactive backend
        "base64",       # must base64 encode SVG
        "1.333",        # px / pt conversion explicitly stated
    ]:
        assert warn in text, f"SKILL.md missing anti-pattern callout: {warn!r}"


def test_skill_md_has_file_verify_dataurl_check() -> None:
    """Must include a FileVerify template that asserts each latex_source
    image has files[fileId].dataURL non-empty."""
    text = SKILL.read_text(encoding="utf-8")
    assert "dataURL" in text and "latex_source" in text
    # The FileVerify recipe must check the svg+xml base64 prefix and
    # a minimum dataURL length.
    assert "data:image/svg+xml;base64," in text
    assert re.search(r"len\(\s*url\s*\)\s*>\s*\d{2,}", text), \
        "SKILL.md FileVerify template must enforce minimum dataURL length"
