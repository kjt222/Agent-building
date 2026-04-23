from __future__ import annotations

import asyncio
import json

import docx
from docx.shared import Pt

from agent.core.loop import LoopConfig, LoopContext, PermissionLevel
from agent.tools_v2.word_tool import WordEditTool, WordReadTool, word_toolset


def _ctx() -> LoopContext:
    return LoopContext(config=LoopConfig())


def _document(path):
    document = docx.Document()
    title = document.add_paragraph("Template Heading")
    title.style = "Title"
    title.runs[0].bold = True
    title.runs[0].font.size = Pt(18)
    document.add_paragraph("Section 1")
    document.add_paragraph("Revenue is 10.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Metric"
    table.cell(0, 1).text = "Value"
    document.save(path)


def test_word_read_reports_paragraphs_tables_and_tracks_guard(tmp_path):
    target = tmp_path / "sample.docx"
    _document(target)
    ctx = _ctx()

    result = asyncio.run(WordReadTool().run({"path": str(target)}, ctx))

    assert result.is_error is False
    data = json.loads(result.content)
    assert data["type"] == "word_read"
    assert data["paragraph_count"] == 3
    assert data["paragraphs"][0]["text"] == "Template Heading"
    assert data["tables"][0]["cells"][0]["text"] == "Metric"
    assert str(target.resolve()) in ctx.scratch["word_read_files"]


def test_word_edit_requires_word_read_first(tmp_path):
    target = tmp_path / "sample.docx"
    _document(target)

    result = asyncio.run(
        WordEditTool().run(
            {
                "path": str(target),
                "ops": [
                    {
                        "op": "replace_text",
                        "paragraph_index": 2,
                        "old": "10",
                        "new": "12",
                    }
                ],
            },
            _ctx(),
        )
    )

    assert result.is_error is True
    assert "Call WordRead first" in result.content
    assert "Revenue is 10" in "\n".join(p.text for p in docx.Document(target).paragraphs)


def test_word_edit_applies_scoped_replace_and_rejects_global_by_default(tmp_path):
    target = tmp_path / "sample.docx"
    _document(target)
    ctx = _ctx()
    asyncio.run(WordReadTool().run({"path": str(target)}, ctx))

    global_result = asyncio.run(
        WordEditTool().run(
            {
                "path": str(target),
                "ops": [{"op": "replace_text", "old": "Revenue", "new": "Sales"}],
            },
            ctx,
        )
    )
    scoped_result = asyncio.run(
        WordEditTool().run(
            {
                "path": str(target),
                "ops": [
                    {
                        "op": "replace_text",
                        "paragraph_index": 2,
                        "old": "10",
                        "new": "12",
                    }
                ],
            },
            ctx,
        )
    )

    assert global_result.is_error is True
    assert "paragraph_index" in global_result.content
    assert scoped_result.is_error is False
    updated = docx.Document(target)
    assert updated.paragraphs[2].text == "Revenue is 12."
    assert str(target.resolve()) in ctx.scratch["edited_files"]


def test_word_edit_copies_paragraph_style_and_inserts_local_paragraph(tmp_path):
    target = tmp_path / "sample.docx"
    _document(target)
    ctx = _ctx()
    asyncio.run(WordReadTool().run({"path": str(target)}, ctx))

    result = asyncio.run(
        WordEditTool().run(
            {
                "path": str(target),
                "ops": [
                    {
                        "op": "copy_paragraph_style",
                        "source_paragraph_index": 0,
                        "paragraph_index": 1,
                    },
                    {
                        "op": "insert_paragraph_after",
                        "paragraph_index": 1,
                        "text": "Inserted local note.",
                        "style": "Normal",
                    },
                ],
            },
            ctx,
        )
    )

    assert result.is_error is False
    updated = docx.Document(target)
    assert updated.paragraphs[1].style.name == updated.paragraphs[0].style.name
    assert updated.paragraphs[1].runs[0].bold is True
    assert updated.paragraphs[2].text == "Inserted local note."
    assert updated.paragraphs[3].text == "Revenue is 10."


def test_word_tool_protocol_flags_are_minimal():
    tools = word_toolset()

    assert set(tools) == {"WordRead", "WordEdit"}
    assert tools["WordRead"].permission_level == PermissionLevel.SAFE
    assert tools["WordEdit"].permission_level == PermissionLevel.NEEDS_APPROVAL
    assert tools["WordRead"].parallel_safe is True
    assert tools["WordEdit"].parallel_safe is False
