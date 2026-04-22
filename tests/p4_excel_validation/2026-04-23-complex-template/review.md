# P4 Complex Excel Template Validation

Runner: Codex

Purpose:

- Test a more realistic Excel task: apply template formatting to a report sheet,
  make scoped formula edits, render the workbook, and preserve unrelated sheets.
- Validate that the P4 Excel path can handle template style transfer without
  exposing additional top-level tools.

Optimization added:

- `ExcelEdit` now supports `copy_range_style`.
- `ExcelEdit` now supports explicit `merge_cells` and `unmerge_cells`.
- `ExcelRead` now reports row heights and column widths for the inspected range.
- The tool surface is unchanged: still only `ExcelRead` and `ExcelEdit` for Excel.

UI conversation:

- UI URL: `http://127.0.0.1:8765/`
- Conversation ID: `conv_20260423_003948_f4051b`
- Profile/model: `doubao-code` / `doubao-seed-2.0-code`

Task:

- Inspect `Template!A1:D3` and `Report!A1:D6`.
- Merge `Report!A1:D1`.
- Copy title style from `Template!A1:D1` to `Report!A1:D1`.
- Copy header style from `Template!A3:D3` to `Report!A3:D3`.
- Set delta formulas in `Report!D4:D6`.
- Render and visually inspect the result.
- Do not change `Notes`.

Observed tool path:

- Tool manifest: `ExcelRead`, `ExcelEdit`, `Glob`, `Read`, `RenderDocument`.
- Calls: `ExcelRead`, `ExcelRead`, `ExcelEdit`, `RenderDocument`.
- `RenderDocument` succeeded and attached 2 image feedback blocks.

Independent workbook check:

- `Report!A1:D1` is merged.
- Title style copied: bold, dark blue fill, centered alignment.
- Header style copied: bold, blue fill.
- `Report` column width and row height match the template.
- `Report!D4 = C4-B4`, `Report!D5 = C5-B5`, `Report!D6 = C6-B6`.
- `Notes!A1` is unchanged.

Artifacts:

- `summary.json`: structured result.
- `conversation.json`: saved UI conversation.
- `events.jsonl`: parsed SSE events.
- `raw_sse.txt`: raw AgentLoop stream.
- `renders/`: PDF and PNG render outputs.
- `ui_conversation_complex_template.png`: UI screenshot with the opened conversation.
