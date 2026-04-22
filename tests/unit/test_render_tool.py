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

