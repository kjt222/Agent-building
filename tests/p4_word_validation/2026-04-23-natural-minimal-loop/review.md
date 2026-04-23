# P4 Word Natural Minimal Loop Validation

Runner: Codex

Purpose:

- Validate the Word MVP using a natural user request, without telling the model
  which tools to call.
- Confirm the framework exposes the Word-specific minimal tool surface and the
  model chooses the read/edit/render loop on its own.

Important test constraint:

- The prompt did not mention `WordRead`, `WordEdit`, `RenderDocument`,
  paragraph indexes, local edit tools, or rendering.
- It only asked to update a Word document, match an existing template heading
  style, change one revenue sentence, preserve the note/table, and check the
  result.

UI conversation:

- UI URL: `http://127.0.0.1:8766/`
- Conversation ID: `conv_20260423_093306_7f75ba`
- Profile/model: `doubao-code` / `doubao-seed-2.0-code`

Observed tool surface:

- `Glob`
- `Read`
- `RenderDocument`
- `WordEdit`
- `WordRead`

Observed tool path:

- `WordRead`
- `WordEdit`
- `WordRead`
- `RenderDocument`

Independent document check:

- Paragraph 1 text: `Revenue Summary`
- Paragraph 1 style: `Heading 1`
- Paragraph 1 bold: `true`
- Paragraph 2 text: `Revenue is 12.`
- Paragraph 3 unchanged: `Notes: keep this paragraph unchanged.`
- Table cells unchanged: `Metric`, `Value`

Visual check:

- `renders/word_natural_task_page_1_1776908156126.png` shows the updated
  heading using the same heading style family as the template and the revenue
  sentence changed to `12`.

Artifacts:

- `summary.json`: structured result.
- `conversation.json`: saved UI conversation.
- `events.jsonl`: parsed SSE events.
- `raw_sse.txt`: raw AgentLoop stream.
- `renders/`: PDF and PNG render outputs.
- `ui_conversation_word_natural.png`: UI screenshot with the opened
  conversation.
