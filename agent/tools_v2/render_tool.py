"""Document rendering tool for vision-in-the-loop workflows."""

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
        "Render PDF/DOCX/XLSX documents to PNG images for visual inspection. "
        "PDF is rendered directly with PyMuPDF. DOCX/XLSX are converted to PDF "
        "with LibreOffice headless first, then rendered. Returns image paths "
        "that the AgentLoop can attach to the next model turn."
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
        if suffix in {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt"}:
            render_source = self._convert_to_pdf(source, output_dir)
            converted_pdf = str(render_source)
        elif suffix != ".pdf":
            raise ValueError(
                f"unsupported document type {suffix!r}; expected PDF/DOCX/XLSX"
            )

        rendered = self._render_pdf(render_source, pages, output_dir, dpi)
        return {
            "type": "rendered_document",
            "source_path": str(source),
            "converted_pdf_path": converted_pdf,
            "pages_requested": pages,
            "total_pages": rendered["total_pages"],
            "images": rendered["images"],
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

