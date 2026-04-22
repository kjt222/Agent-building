"""Document/image rendering tool for vision-in-the-loop workflows."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.tools_v2.primitives import _ToolBase


class RenderDocumentTool(_ToolBase):
    name = "RenderDocument"
    description = (
        "Render PDF/DOCX/XLSX documents or inspect image files for visual feedback. "
        "PDF is rendered directly with PyMuPDF. DOCX/XLSX are converted to PDF "
        "with LibreOffice headless first, then rendered. Existing PNG/JPG/WebP "
        "images can be returned directly or cropped/zoomed like a magnifier. "
        "Returns image paths that the AgentLoop can attach to the next model turn."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Document path"},
            "pages": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "1-indexed pages to render. Defaults to [1].",
            },
            "output_dir": {
                "type": "string",
                "description": "Directory for rendered PNGs. Defaults to tmp/renders.",
            },
            "dpi": {"type": "integer", "default": 150},
            "max_pages": {"type": "integer", "default": 3},
            "regions": {
                "type": "array",
                "description": (
                    "Optional pixel crop boxes for magnified inspection. "
                    "Each item: {x,y,width,height,scale?,page?,name?}. For PDF, "
                    "coordinates are in rendered page pixels."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                        "scale": {"type": "number", "default": 2},
                        "page": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                    "required": ["x", "y", "width", "height"],
                },
            },
        },
        "required": ["path"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        try:
            result = self._run_sync(input)
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")
        return self._ok(json.dumps(result, ensure_ascii=False, indent=2))

    def _run_sync(self, input: dict) -> dict:
        source = Path(str(input["path"])).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"file not found: {source}")
        if source.is_dir():
            raise IsADirectoryError(str(source))

        output_dir = Path(str(input.get("output_dir") or "tmp/renders"))
        output_dir.mkdir(parents=True, exist_ok=True)
        dpi = max(36, min(int(input.get("dpi") or 150), 300))
        max_pages = max(1, min(int(input.get("max_pages") or 3), 10))
        requested_pages = input.get("pages") or [1]
        pages = [int(p) for p in requested_pages if int(p) > 0][:max_pages]
        if not pages:
            pages = [1]

        render_source = source
        converted_pdf: str | None = None
        suffix = source.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            return self._inspect_image(source, output_dir, input.get("regions") or [])
        if suffix in {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"}:
            render_source = self._convert_to_pdf(source, output_dir)
            converted_pdf = str(render_source)
        elif suffix != ".pdf":
            raise ValueError(
                f"unsupported document type {suffix!r}; expected PDF/DOCX/XLSX"
            )

        rendered = self._render_pdf(render_source, pages, output_dir, dpi)
        regions = self._crop_regions(
            rendered["images"],
            input.get("regions") or [],
            output_dir,
            source.stem,
        )
        return {
            "type": "rendered_document",
            "source_path": str(source),
            "converted_pdf_path": converted_pdf,
            "pages_requested": pages,
            "total_pages": rendered["total_pages"],
            "images": rendered["images"],
            "regions": regions,
        }

    def _inspect_image(
        self,
        source: Path,
        output_dir: Path,
        regions: list[dict],
    ) -> dict:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Pillow is not installed") from exc

        with Image.open(source) as img:
            width, height = img.size
        image = {
            "page": 1,
            "width": width,
            "height": height,
            "media_type": self._image_media_type(source),
            "rendered_image_path": str(source),
        }
        crops = self._crop_regions([image], regions, output_dir, source.stem)
        return {
            "type": "rendered_image",
            "source_path": str(source),
            "total_pages": 1,
            "images": [image],
            "regions": crops,
        }

    def _convert_to_pdf(self, source: Path, output_dir: Path) -> Path:
        soffice = self._find_soffice()
        if not soffice:
            raise RuntimeError(
                "LibreOffice/soffice not found; install LibreOffice for DOCX/XLSX rendering"
            )
        cmd = [
            soffice,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "LibreOffice conversion failed: "
                f"stdout={proc.stdout.strip()} stderr={proc.stderr.strip()}"
            )
        pdf = output_dir / f"{source.stem}.pdf"
        if not pdf.exists():
            matches = list(output_dir.glob(f"{source.stem}*.pdf"))
            if matches:
                pdf = matches[0]
        if not pdf.exists():
            raise RuntimeError("LibreOffice conversion did not produce a PDF")
        return pdf

    def _find_soffice(self) -> str | None:
        for name in ("soffice", "libreoffice"):
            found = shutil.which(name)
            if found:
                return found
        candidates = [
            Path("C:/Program Files/LibreOffice/program/soffice.exe"),
            Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None

    def _render_pdf(
        self,
        pdf_path: Path,
        pages: list[int],
        output_dir: Path,
        dpi: int,
    ) -> dict[str, Any]:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("PyMuPDF is not installed") from exc

        doc = fitz.open(str(pdf_path))
        total_pages = len(doc)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        stamp = int(time.time() * 1000)
        images: list[dict[str, Any]] = []
        try:
            for page_number in pages:
                if page_number < 1 or page_number > total_pages:
                    continue
                page = doc[page_number - 1]
                pix = page.get_pixmap(matrix=matrix)
                out_path = (
                    output_dir
                    / f"{pdf_path.stem}_page_{page_number}_{stamp}.png"
                )
                pix.save(str(out_path))
                images.append({
                    "page": page_number,
                    "width": pix.width,
                    "height": pix.height,
                    "media_type": "image/png",
                    "rendered_image_path": str(out_path),
                })
        finally:
            doc.close()

        if not images:
            raise ValueError(
                f"no valid pages rendered from {pdf_path}; total_pages={total_pages}"
            )
        return {"total_pages": total_pages, "images": images}

    def _crop_regions(
        self,
        images: list[dict[str, Any]],
        regions: list[dict],
        output_dir: Path,
        source_stem: str,
    ) -> list[dict[str, Any]]:
        if not regions:
            return []
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Pillow is not installed") from exc

        crops: list[dict[str, Any]] = []
        by_page = {int(item.get("page") or 1): item for item in images}
        for idx, region in enumerate(regions[:10], 1):
            page_number = int(region.get("page") or 1)
            image_info = by_page.get(page_number)
            if not image_info:
                continue
            image_path = Path(str(image_info["rendered_image_path"]))
            if not image_path.exists():
                continue

            x = max(0, int(region["x"]))
            y = max(0, int(region["y"]))
            width = max(1, int(region["width"]))
            height = max(1, int(region["height"]))
            scale = max(1.0, min(float(region.get("scale") or 2.0), 8.0))
            label = str(region.get("name") or f"region_{idx}")
            safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)

            with Image.open(image_path) as img:
                right = min(img.width, x + width)
                bottom = min(img.height, y + height)
                if right <= x or bottom <= y:
                    continue
                crop = img.crop((x, y, right, bottom))
                if scale != 1.0:
                    crop = crop.resize(
                        (
                            max(1, int(crop.width * scale)),
                            max(1, int(crop.height * scale)),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                out_path = (
                    output_dir
                    / f"{source_stem}_page_{page_number}_{safe_label}_crop.png"
                )
                crop.save(out_path)
                crops.append({
                    "page": page_number,
                    "region": {
                        "x": x,
                        "y": y,
                        "width": right - x,
                        "height": bottom - y,
                        "scale": scale,
                        "name": label,
                    },
                    "width": crop.width,
                    "height": crop.height,
                    "media_type": "image/png",
                    "rendered_image_path": str(out_path),
                })
        return crops

    def _image_media_type(self, path: Path) -> str:
        suffix = path.suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(suffix, "image/png")
