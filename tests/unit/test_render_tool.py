from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent.core.loop import LoopConfig, LoopContext
from agent.tools_v2.render_tool import RenderDocumentTool


def test_render_document_missing_file_returns_error(tmp_path: Path):
    tool = RenderDocumentTool()
    ctx = LoopContext(config=LoopConfig())

    result = asyncio.run(tool.run({"path": str(tmp_path / "missing.pdf")}, ctx))

    assert result.is_error is True
    assert "file not found" in str(result.content)


def test_render_document_pdf_page_to_png(tmp_path: Path):
    import fitz

    pdf = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    page.insert_text((24, 48), "P3 render smoke")
    doc.save(str(pdf))
    doc.close()

    tool = RenderDocumentTool()
    ctx = LoopContext(config=LoopConfig())
    result = asyncio.run(tool.run({
        "path": str(pdf),
        "pages": [1],
        "output_dir": str(tmp_path / "renders"),
        "dpi": 72,
    }, ctx))

    assert result.is_error is False
    payload = json.loads(str(result.content))
    assert payload["type"] == "rendered_document"
    assert payload["total_pages"] == 1
    [image] = payload["images"]
    assert image["page"] == 1
    assert image["media_type"] == "image/png"
    assert Path(image["rendered_image_path"]).exists()


def test_render_document_can_crop_image_regions(tmp_path: Path):
    from PIL import Image, ImageDraw

    image = tmp_path / "source.png"
    img = Image.new("RGB", (120, 80), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((10, 10, 50, 50), fill="red")
    draw.rectangle((70, 20, 110, 60), fill="blue")
    img.save(image)

    tool = RenderDocumentTool()
    ctx = LoopContext(config=LoopConfig())
    result = asyncio.run(tool.run({
        "path": str(image),
        "output_dir": str(tmp_path / "renders"),
        "regions": [
            {
                "x": 65,
                "y": 15,
                "width": 50,
                "height": 50,
                "scale": 3,
                "name": "blue_detail",
            }
        ],
    }, ctx))

    assert result.is_error is False
    payload = json.loads(str(result.content))
    assert payload["type"] == "rendered_image"
    [region] = payload["regions"]
    assert region["region"]["name"] == "blue_detail"
    assert region["width"] == 150
    assert region["height"] == 150
    assert Path(region["rendered_image_path"]).exists()


def test_render_document_can_crop_rendered_pdf_page(tmp_path: Path):
    import fitz

    pdf = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=120)
    page.insert_text((24, 48), "P3 render smoke")
    page.draw_rect(fitz.Rect(120, 40, 180, 100), fill=(0.7, 0.8, 1.0))
    doc.save(str(pdf))
    doc.close()

    tool = RenderDocumentTool()
    ctx = LoopContext(config=LoopConfig())
    result = asyncio.run(tool.run({
        "path": str(pdf),
        "pages": [1],
        "output_dir": str(tmp_path / "renders"),
        "dpi": 72,
        "regions": [
            {
                "page": 1,
                "x": 110,
                "y": 30,
                "width": 80,
                "height": 80,
                "scale": 2,
                "name": "right_box",
            }
        ],
    }, ctx))

    assert result.is_error is False
    payload = json.loads(str(result.content))
    [region] = payload["regions"]
    assert region["page"] == 1
    assert region["region"]["name"] == "right_box"
    assert Path(region["rendered_image_path"]).exists()
