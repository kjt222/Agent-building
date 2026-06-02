"""Toy verifier for .docx structural integrity.

Designed to plug into ``VerifiedTaskRunner``:

    {
        "passed": bool,
        "violations": [str, ...],
        "repair_hints": [str, ...],
        "metrics": {...},
    }

Checks (slice 1):

- TOC present if requested; TOC entries cover every Heading 1..N text.
- TOC body lines are not contaminated by stray body sentences.
- Every paragraph styled Heading 1..9 carries a non-empty title.
- No heading-styled paragraph sits inside a table cell.
- Field cache freshness: if the document declares fields, the on-disk cache
  is non-empty (Word writes a result child element after Update).

The verifier reads .docx as a zip + XML so it works without a Word runtime.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


@dataclass
class WordVerifierResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    repair_hints: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": list(self.violations),
            "repair_hints": list(self.repair_hints),
            "metrics": dict(self.metrics),
        }


def _q(name: str) -> str:
    return f"{{{W_NS}}}{name}"


def _read_document_xml(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        with zf.open("word/document.xml") as fh:
            return ET.parse(fh).getroot()


def _paragraph_text(paragraph: ET.Element) -> str:
    return "".join(t.text or "" for t in paragraph.iter(_q("t")))


def _paragraph_style(paragraph: ET.Element) -> str | None:
    p_pr = paragraph.find(_q("pPr"))
    if p_pr is None:
        return None
    style_el = p_pr.find(_q("pStyle"))
    if style_el is None:
        return None
    return style_el.get(_q("val"))


_HEADING_STYLE_RE = re.compile(r"^(?:Heading|heading|标题)\s*(\d+)\s*$")


def _heading_level_from_style(style: str | None) -> int | None:
    if not style:
        return None
    match = _HEADING_STYLE_RE.match(style.replace("\xa0", " "))
    if match:
        return int(match.group(1))
    if style.lower().startswith("heading") and style[-1:].isdigit():
        try:
            return int(style[-1])
        except ValueError:
            return None
    return None


def _is_in_table(paragraph: ET.Element, all_table_cells: list[ET.Element]) -> bool:
    for cell in all_table_cells:
        for desc in cell.iter(_q("p")):
            if desc is paragraph:
                return True
    return False


def _field_blocks(root: ET.Element) -> list[dict[str, Any]]:
    """Return one dict per field instruction, with code text and a freshness flag.

    Word stores fields either as a sequence of fldChar(begin)/instrText/.../fldChar(end)
    or as a single ``<w:fldSimple w:instr="...">`` element. After Fields.Update the
    end-fldChar's preceding run carries the cached result text.
    """
    fields: list[dict[str, Any]] = []

    for fld_simple in root.iter(_q("fldSimple")):
        instr = fld_simple.get(_q("instr")) or ""
        text = "".join(t.text or "" for t in fld_simple.iter(_q("t")))
        fields.append({
            "code": instr.strip(),
            "cache": text,
            "fresh": bool(text.strip()),
            "kind": "fldSimple",
        })

    fld_chars = list(root.iter(_q("fldChar")))
    starts = [c for c in fld_chars if c.get(_q("fldCharType")) == "begin"]
    if starts:
        body = root.find(_q("body"))
        if body is None:
            return fields
        all_runs = list(body.iter(_q("r")))
        for begin in starts:
            try:
                begin_idx = next(i for i, r in enumerate(all_runs) if begin in list(r))
            except StopIteration:
                continue
            instr_parts: list[str] = []
            cache_parts: list[str] = []
            saw_separate = False
            saw_end = False
            for run in all_runs[begin_idx + 1:]:
                ftype = None
                fld = run.find(_q("fldChar"))
                if fld is not None:
                    ftype = fld.get(_q("fldCharType"))
                if ftype == "separate":
                    saw_separate = True
                    continue
                if ftype == "end":
                    saw_end = True
                    break
                instr_text = run.find(_q("instrText"))
                if instr_text is not None:
                    instr_parts.append(instr_text.text or "")
                    continue
                if saw_separate:
                    for t in run.iter(_q("t")):
                        cache_parts.append(t.text or "")
            if saw_end or instr_parts:
                fields.append({
                    "code": "".join(instr_parts).strip(),
                    "cache": "".join(cache_parts),
                    "fresh": bool("".join(cache_parts).strip()),
                    "kind": "fldChar",
                })
    return fields


def verify_word_document(
    path: Path,
    *,
    require_toc: bool | None = None,
) -> WordVerifierResult:
    """Verify a .docx file. ``require_toc=None`` means auto-detect (require if a TOC
    field exists in the doc)."""

    path = Path(path).expanduser().resolve()
    if not path.exists():
        return WordVerifierResult(
            passed=False,
            violations=[f"missing_file:{path}"],
            repair_hints=["produce the document at the requested path"],
        )

    try:
        root = _read_document_xml(path)
    except Exception as exc:
        return WordVerifierResult(
            passed=False,
            violations=[f"cannot_read_docx:{type(exc).__name__}"],
            repair_hints=["regenerate the .docx; current file is not a valid OOXML package"],
        )

    body = root.find(_q("body"))
    if body is None:
        return WordVerifierResult(
            passed=False,
            violations=["empty_body"],
            repair_hints=["the document has no body; regenerate it from scratch"],
        )

    paragraphs = list(body.iter(_q("p")))
    table_cells = list(body.iter(_q("tc")))

    headings: list[tuple[int, str, ET.Element]] = []
    headings_in_table = 0
    empty_headings = 0
    for paragraph in paragraphs:
        style = _paragraph_style(paragraph)
        level = _heading_level_from_style(style)
        if level is None:
            continue
        text = _paragraph_text(paragraph).strip()
        if not text:
            empty_headings += 1
            continue
        if _is_in_table(paragraph, table_cells):
            headings_in_table += 1
            continue
        headings.append((level, text, paragraph))

    fields = _field_blocks(root)
    toc_fields = [f for f in fields if f["code"].lstrip().upper().startswith("TOC")]
    has_toc = bool(toc_fields)

    violations: list[str] = []
    repair_hints: list[str] = []

    if require_toc is True and not has_toc:
        violations.append("missing_toc_field")
        repair_hints.append(
            "Use WordRuntimeEdit op=add_toc to insert a {{ TOC }} field; do not paste literal TOC text."
        )

    if has_toc:
        toc_cache = "\n".join(f["cache"] for f in toc_fields)
        toc_lines = [line.strip() for line in toc_cache.splitlines() if line.strip()]
        if not toc_lines:
            violations.append("toc_cache_empty")
            repair_hints.append(
                "TOC field exists but cache is empty; refresh fields after editing "
                "(WordRuntimeEdit op=refresh_fields)."
            )
        else:
            covered = []
            uncovered = []
            for level, text, _p in headings:
                hit = any(text in line for line in toc_lines)
                if hit:
                    covered.append((level, text))
                else:
                    uncovered.append((level, text))
            if uncovered:
                violations.append("toc_missing_headings")
                repair_hints.append(
                    "Some headings are not in the TOC; run WordRuntimeEdit "
                    "op=refresh_fields to rebuild the TOC cache. "
                    f"Examples: {', '.join(t for _l, t in uncovered[:3])}"
                )
            stray = []
            for line in toc_lines:
                if any(text in line for _l, text in headings):
                    continue
                if len(line) > 60 and "." not in line[-10:]:
                    stray.append(line[:80])
            if stray:
                violations.append("toc_contains_orphan_body_text")
                repair_hints.append(
                    "TOC cache contains lines that look like body sentences. "
                    "Do not edit TOC text directly; use WordRuntimeEdit "
                    "op=refresh_fields after structural edits."
                )

    if empty_headings:
        violations.append("empty_heading_paragraphs")
        repair_hints.append(
            f"{empty_headings} heading-styled paragraph(s) are empty; either delete them "
            "or set their text via WordRuntimeEdit op=set_heading_text."
        )

    if headings_in_table:
        violations.append("heading_inside_table_cell")
        repair_hints.append(
            f"{headings_in_table} heading paragraph(s) sit inside a table cell. "
            "Restyle the offending paragraphs to a body style."
        )

    fresh_fields = sum(1 for f in fields if f["fresh"])
    stale_fields = [f for f in fields if not f["fresh"]]
    if fields and fresh_fields == 0:
        violations.append("all_fields_unrefreshed")
        repair_hints.append(
            "No field has a refreshed cache. Run WordRuntimeEdit op=refresh_fields "
            "(or save through Word) so {{ TOC }}, {{ PAGE }}, {{ REF }} render."
        )

    metrics = {
        "heading_count": len(headings),
        "empty_heading_count": empty_headings,
        "heading_in_table_count": headings_in_table,
        "field_count": len(fields),
        "fresh_field_count": fresh_fields,
        "stale_field_count": len(stale_fields),
        "toc_field_count": len(toc_fields),
        "paragraph_count": len(paragraphs),
    }

    return WordVerifierResult(
        passed=not violations,
        violations=violations,
        repair_hints=repair_hints,
        metrics=metrics,
    )
