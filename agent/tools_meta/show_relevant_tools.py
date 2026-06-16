"""show_relevant_tools — meta-tier router.

When the model faces an unfamiliar file type, external app, or domain it
doesn't have built-in tooling for, it calls this tool with a one-sentence
task summary. The router returns a narrowed list of {name, description}
the model should consider using.

Design notes:
- The router does NOT enable / disable tools at the loop level. All
  capability-tier tools are already registered. The router is a
  *discovery aid*: it tells the model "for THIS task, these are the
  tools to look at." The model then calls them by name (they exist).
- This is intentionally simple matching, not a learned router. Keyword
  bundles per domain. If a domain isn't in the catalog, the router
  returns the meta tier itself + a hint to use ``Bash`` for novel
  domains.
- Update the catalog when a new capability-tier subdomain is added.
- No internal retry / fuzzy matching ladder — if the model's task
  summary is too vague, return everything and let the model pick.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Static catalog. Each entry: (keyword_set, [(tool_name, one_line_desc)])
# ---------------------------------------------------------------------------

_CATALOG: list[tuple[list[str], list[tuple[str, str]]]] = [
    # 2026-06-09: dedicated obsidian_* capability tools DO exist and are
    # registered in full-access mode (server.py) and the generic factory.
    # The prior "there is NO obsidian tool, use Bash" entry was a stale
    # P14.6.16 lie that drove agents back to hand-rolling lz-string + a
    # fragile katex path. Surface the real tools instead; SKILL.md is the
    # how-to, but these are the safe primitives to call by name.
    (
        [
            "obsidian", "excalidraw", "canvas", "vault",
            "公式", "推导", "笔记",
        ],
        [
            ("obsidian_read_excalidraw_canvas",
             "Read an Obsidian .excalidraw.md canvas: decodes the "
             "lz-string ## Drawing fence to elements[], files{}, "
             "element_links, appState — never parse the compressed blob "
             "by hand."),
            ("obsidian_find_pdf_text_anchor",
             "Locate a string (e.g. '(6)') inside a PDF embedded in the "
             "canvas and map it to canvas coords, returning a "
             "suggested_insert_xy beside the match. Use BEFORE placing an "
             "annotation so it lands next to the right formula."),
            ("obsidian_add_formula_annotation",
             "HIGH-LEVEL one-call: annotate a formula — give latex + an "
             "explanation + where it is (anchor_query like '(1)' and/or "
             "target_xy); it renders the SVG, places the formula image, "
             "wraps the explanation below, draws an arrow at the formula, "
             "and groups all three. Prefer this for formula annotations."),
            ("obsidian_write_excalidraw_elements",
             "Lower-level: append/patch elements safely (re-encodes the "
             "fence, keeps files{} consistent). For a formula, add an image "
             "element with a `latex` field — the tool renders it to an SVG "
             "dataURL automatically; do NOT hand-wire fileId/dataURL/katex."),
            ("obsidian_refresh_note",
             "Close→reopen the note via the Local REST API so an OPEN "
             "Obsidian drops its stale in-memory buffer and re-reads your "
             "write from disk (beats the open-tab autosave-clobber race). "
             "Call after write_elements."),
            ("__skill__obsidian-excalidraw",
             "Read skills/obsidian-excalidraw/SKILL.md for the full "
             "workflow: read → find_pdf_text_anchor → "
             "write_elements(latex field) → refresh_note."),
        ],
    ),
    # Office documents — placeholder; mapped to legacy v2 tools until
    # the edit_anything dispatcher lands.
    (
        [
            "word", "docx", "微软", "office", "文档", "论文",
            "endnote", "引用", "注脚", "脚注", "字段", "field",
        ],
        [
            ("WordRuntimeEdit",
             "Edit a .docx via Microsoft Word COM automation. Supports "
             "the full Word object model including fields, footnotes, "
             "and EndNote plugin commands when EndNote is installed."),
            ("WordRead",
             "Read a .docx file's structure (paragraphs, runs, fields, "
             "footnotes, comments)."),
        ],
    ),
    # Excel
    (
        ["excel", "xlsx", "表格", "spreadsheet"],
        [
            ("ExcelRuntimeEdit",
             "Edit a .xlsx via Microsoft Excel COM automation."),
            ("ExcelRead", "Read a .xlsx file's structure."),
        ],
    ),
    # KLayout
    (
        ["klayout", "gds", "版图", "layout", "drc"],
        [
            ("KLayout", "Run KLayout in batch mode for GDS layout edits "
             "and DRC checks."),
        ],
    ),
    # Verification / acceptance
    (
        ["verify", "验收", "oracle", "accept", "check"],
        [
            ("Verify", "Run an L2/L3 acceptance oracle from "
             "agent/acceptance/oracles on a delivered artifact."),
            ("FileVerify", "Structural verification (file exists, parses, "
             "has expected fields)."),
        ],
    ),
]

@dataclass
class ToolSuggestion:
    name: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def show_relevant_tools_sync(task_summary: str) -> list[ToolSuggestion]:
    """Return tool suggestions for ``task_summary``.

    Matching is substring-based (case-insensitive). Substring (rather
    than token set intersection) avoids the CJK-tokenization trap where
    ``"在画板里加公式推导"`` is one token and so misses the catalog's
    shorter keywords ``"公式"`` / ``"推导"``.
    """
    haystack = (task_summary or "").lower()
    seen: set[str] = set()
    out: list[ToolSuggestion] = []
    for keyword_set, tools in _CATALOG:
        if not any(k.lower() in haystack for k in keyword_set):
            continue
        for name, desc in tools:
            if name in seen:
                continue
            seen.add(name)
            out.append(ToolSuggestion(name=name, description=desc))
    if not out:
        # Nothing matched. Tell the model to fall back to primitives.
        return [
            ToolSuggestion(
                name="Bash",
                description="No specialized tools matched this task. "
                            "Use Bash to invoke any installed CLI / Python "
                            "script. Read/Write/Edit/Glob/Grep are always "
                            "available for filesystem work.",
            ),
        ]
    return out


# ---------------------------------------------------------------------------
# _ToolBase-shaped wrapper
# ---------------------------------------------------------------------------


class ShowRelevantToolsTool:
    # Late import — PermissionLevel lives in core/loop, which we don't
    # want to import at module load time for cheap unit tests.
    @property
    def permission_level(self):  # type: ignore[no-untyped-def]
        from agent.core.loop import PermissionLevel
        return PermissionLevel.SAFE

    name = "show_relevant_tools"
    description = (
        "Discover specialized tools for unfamiliar file types or external "
        "applications. The generic primitives you start with (Read, Write, "
        "Edit, Bash) work for plain text, but they will SILENTLY CORRUPT "
        "files with binary payloads, embedded base64 blobs, or "
        "plugin-managed schemas — examples include .docx, .xlsx, .pptx, "
        ".gds, .excalidraw.md (compressed-json fence). For any task that "
        "touches such a file or drives an external app (Obsidian, MS "
        "Office, KLayout, etc.), call this FIRST with a one-sentence task "
        "summary. It returns {name, description} for tools that handle "
        "the format safely; those tools are already registered and "
        "immediately callable by name."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "task_summary": {
                "type": "string",
                "description": (
                    "Brief description of what you are trying to do "
                    "(e.g. 'insert formula derivation into Obsidian "
                    "Excalidraw canvas')."
                ),
            },
        },
        "required": ["task_summary"],
    }
    parallel_safe = True

    async def run(self, input: dict, ctx) -> Any:
        from agent.core.loop import ToolResultBlock

        summary = str(input.get("task_summary") or "")
        suggestions = show_relevant_tools_sync(summary)
        body = {
            "task_summary": summary,
            "suggestions": [s.to_dict() for s in suggestions],
        }
        return ToolResultBlock(
            tool_use_id="",
            content=json.dumps(body, ensure_ascii=False, indent=2),
            is_error=False,
        )
