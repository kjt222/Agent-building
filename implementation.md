# Implementation Log

## 2026-04-22 - Codex

Codex ran real browser UI validation for representative AgentLoop v2 tasks. The results are stored under:

`tests/ui_conversation_results/2026-04-22-ui-tasks/`

Validated tasks:

- Runtime metadata direct answer: completed with no tool calls.
- Repository inspection: completed with file tool calls.
- SWE-style HTML bugfix: completed with `Read`/edit flow and browser `Verify`.
- Frontend generation: completed with file creation, read-back, and browser `Verify`.

Artifacts added:

- `bad_snake_fixture.html`: intentionally broken fixture, then fixed by the UI-driven agent task.
- `claude_home_replica.html`: generated frontend artifact from the UI task.
- `*.txt` and `*.png`: saved visible conversation text and screenshots.
- `ui_task_summary.json`: structured run summary.
- `ui_task_review.md`: human review of the UI task run.

Current engineering notes:

- AgentLoop v2 is usable through the main UI path.
- Session/runtime metadata is injected into the model context, so direct model/profile questions can be answered without a tool call.
- Browser `Verify` is available for HTML/CSS/JS/UI artifacts.
- The current UI shows Activity summaries, but saved plain text does not preserve expanded tool details.
- A full P3 vision loop is not complete yet; current `Verify` covers browser-rendered HTML/URL checks, not Office rendering or image feedback into the next model turn.

## 2026-04-22 - Codex - P1 Activity Trace Closeout

Codex completed the P1 Activity trace persistence/export slice.

Changed files:

- `agent/storage/database.py`: schema version bumped to 3; added `activity_traces` table and trace read/write methods.
- `agent/storage/conversation_adapter.py`: added conversation-level methods for adding, listing, and fetching activity traces.
- `agent/ui/server.py`: `/api/agent_chat_v2` now persists per-turn trace data for real UI conversations, including SSE events, assistant text, tool list, capability scope, runtime/provider metadata, system prompt hash, and AgentLoop JSONL records.
- `tests/unit/test_agent_chat_v2_contract.py`: added coverage that a UI-style conversation persists trace data and can export it.

New endpoints:

- `GET /api/conversations/{conv_id}/activity_traces`
- `GET /api/conversations/{conv_id}/activity_traces/{request_id}`
- `GET /api/conversations/{conv_id}/activity_traces/{request_id}/export`

Verification:

- `pytest tests/unit/test_agent_chat_v2_contract.py tests/unit/test_agent_loop.py -q`
- Result: `22 passed`

P3 status after this change:

- Not complete.
- Completed P3 slice: browser `Verify` for HTML/URL artifacts.
- Remaining P3 scope: Office renderers, generated-image render handling, screenshot/image-block feedback into the next model turn, and model-controlled self-review loop.

## 2026-04-22 - Codex - P3 Render Feedback Slice

Codex continued P3 with the next vision-in-the-loop foundation slice.

Changed files:

- `agent/core/loop.py`: tool results that contain `screenshot_path`, `rendered_image_path`, or base64 image payloads are converted into `ImageBlock`s and attached to the next model turn.
- `agent/models/openai_adapter_v2.py`: chat-completions conversion now supports tool results followed by multimodal image feedback.
- `agent/models/openai_responses_adapter.py`: Responses API conversion now supports function-call outputs followed by multimodal image feedback.
- `agent/ui/server.py`: Activity emits `image_feedback` when rendered images are attached; the system prompt now tells the model to inspect rendered screenshots and to use `RenderDocument` for document layout.
- `agent/tools_v2/render_tool.py`: added `RenderDocument` for PDF rendering and LibreOffice-backed DOCX/XLSX/PPTX-to-PDF rendering when LibreOffice is available.
- `agent/tools_v2/primitives.py`: registered `RenderDocument` in the v2 full toolset.
- `tests/unit/test_agent_loop.py`: covers automatic screenshot attachment into the next model call.
- `tests/unit/test_adapter_conversion.py`: covers tool-result-plus-image conversion for Chat Completions and Responses API.
- `tests/unit/test_render_tool.py`: covers PDF-to-PNG rendering.

Verification:

- `pytest tests/unit/test_render_tool.py tests/unit/test_agent_loop.py tests/unit/test_adapter_conversion.py tests/unit/test_agent_chat_v2_contract.py -q`
- Result: `39 passed`

Current P3 status:

- Browser verification: complete for HTML/URL artifacts.
- Render feedback: partially complete. PDF rendering works directly; Office formats depend on installed LibreOffice.
- Image feedback into the next model turn: complete for tool-result image paths/base64 payloads.
- Remaining: native Excel COM rendering path, generated-image tool integration, active-window/script screenshot path, and a higher-level self-review policy/eval loop.
