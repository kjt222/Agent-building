# P4 Word Complex Short-Prompt Validation

Runner: Codex

Purpose:

- Use very short, non-technical user prompts.
- Do not name tools, local edit operations, rendering, or verification steps in
  the prompt.
- Compare current Word tool coverage and final acceptance-summary behavior.

Prompt shape:

- "Teacher says this thesis format is wrong, help me clean it up: <path>"
- "Need TOC, heading levels, tables, footnotes, header/footer; do not change
  the meaning."

Tool surface:

- `Glob`
- `Read`
- `RenderDocument`
- `WordEdit`
- `WordRead`

Post-change smoke validation:

- Conversation ID: `conv_20260423_113313_8ffc41`
- Profile/model: `doubao-code` / `doubao-seed-2.0-code`
- Tool path: `WordRead`, `WordEdit`, `WordEdit`, `WordRead`, `WordEdit`,
  `RenderDocument`
- The final assistant answer included `验收摘要` with Completed,
  Not completed/unsupported, and Evidence sections.

Independent document check:

- Heading styles: chapter headings became `Heading 1`, subsection headings
  became `Heading 2`, and the third-level heading became `Heading 3`.
- TOC field: present.
- Footnotes part: present.
- Header text: thesis title present.
- Footer/page field: present.
- Existing table: preserved.
- Abstract/reference sections: preserved.
- New requested table: not detected in this smoke run.

Framework observations:

- The same short prompt now has enough structured Word operations to complete
  TOC field insertion, footnote insertion, header/footer, page number, and
  heading-level edits without exposing Bash, Write, raw Edit, or DocxEdit.
- The acceptance-summary hook forced a final completion/non-completion report
  after artifact edits.
- The loop now reserves a bounded final-answer path after tool calls at the
  iteration cap and blocks further tool calls once the cap has been exceeded.

Remaining gap:

- Models may still choose not to use `insert_table_after` even when the prompt
  asks for a table. The tool exists, but selection behavior needs more evals
  before adding more capability.
