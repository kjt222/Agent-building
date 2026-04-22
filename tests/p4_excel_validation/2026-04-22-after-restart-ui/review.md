# P4 LibreOffice After-Restart Validation

Runner: Codex

Purpose:

- Confirm LibreOffice is installed and available after restart.
- Confirm `RenderDocument` can render an Excel workbook through LibreOffice.
- Confirm a real `/api/agent_chat_v2` UI conversation can call `RenderDocument` successfully and receive image feedback.

Environment checks:

- `soffice --headless --version` succeeded.
- Resolved command: `C:\Program Files\LibreOffice\program\soffice.com`
- Version: `LibreOffice 26.2.2.2`

Direct tool check:

- Input workbook: `tests/p4_excel_validation/2026-04-22-ui-history-ascii/budget_validation_ascii.xlsx`
- Output directory: `tests/p4_excel_validation/2026-04-22-after-restart-render/`
- `RenderDocument` returned `is_error=false`.
- It produced both a PDF and a rendered PNG.

UI conversation check:

- UI URL: `http://127.0.0.1:8765/`
- Conversation ID: `conv_20260422_213609_a8a3ae`
- Prompt: ask the agent to render the Excel workbook and visually inspect the result.
- Called tools: `RenderDocument`
- Tool result status: `done`
- Image feedback count: `1`
- `LibreOffice/soffice not found`: `false`

Observed model answer:

- `RenderDocument succeeded.`
- Header row is bold with light green fill.
- Revenue is `12`.
- Cost is `4`.

Artifacts:

- `summary.json`: structured result.
- `raw_sse.txt`: raw SSE stream.
- `events.jsonl`: parsed SSE events.
- `conversation.json`: saved conversation payload.
- `ui_conversation_after_restart.png`: UI screenshot with the opened conversation.
- `ui_history_after_restart.png`: UI sidebar screenshot.
