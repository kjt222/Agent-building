"""Headless renderers feeding L3 vision_judge.

Each renderer takes an artifact path (or decoded scene) and writes a PNG.
Renderers are intentionally Pillow-only — no cairosvg / Inkscape / GTK
runtime — so they work on bare Windows venvs. SVG dataURLs embedded inside
Excalidraw scenes are rendered as placeholder boxes annotated with the
latex_source text; the vision_judge model is told this is a layout
approximation, not a pixel-perfect screenshot of the target app.
"""
