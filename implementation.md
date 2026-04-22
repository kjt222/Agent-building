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

Additional P3 replay:

- `tests/p3_vision_loop_results/2026-04-22-render-feedback/`
- The replay creates `layout_fixture.pdf`, calls `RenderDocument`, and verifies the second model call receives one PNG `ImageBlock`.
- Result: passed.

## Image Generation Architecture Notes - 2026-04-22 - Codex

The image-generation tool must not be implemented as a one-shot "prompt in, image out" utility only. The user requirement is iterative visual production:

- Detail correction: many failures are local details, not the whole composition. The system needs crop/zoom inspection and inpainting/editing so small regions can be revised without changing the full image.
- Visual inspection: generated images should be fed back into the model as image blocks. The agent should be able to inspect the whole image and selected crop regions before deciding whether to revise.
- Magnifier workflow: image review should support region crops by coordinates or semantic boxes, producing enlarged inspection images that can move across the source image.
- Identity/style consistency: recurring assets such as a digital human spokesperson need persistent references. Store canonical reference images, style descriptors, seed/parameter metadata when available, and use image-to-image/reference inputs for later generations.
- Series consistency: a campaign or storyboard should have an asset profile that carries subject identity, wardrobe, background style, palette, camera/lens language, and negative constraints across turns.
- Minimal-change edits: prefer img2img/inpainting for detail fixes; full regeneration should be a deliberate fallback because it risks changing identity, layout, or style.

Implication for P5:

- `ImageGenerate` is only the first tool.
- Required follow-up tools are `ImageInspect`, `ImageCrop`/magnifier, `ImageEdit`/inpaint, `ImageCompare`, and an asset-reference store.
- Cost/budget hooks remain necessary because iterative image review can multiply calls quickly.

## 2026-04-22 - Codex - P3 Minimal Tool Completion

Codex kept the P3 tool surface minimal instead of adding separate image/crop/inspect tools.

Tool boundary:

- `Verify`: browser/HTML/URL verification and screenshot capture.
- `RenderDocument`: document rendering plus image-file inspection and crop/zoom magnifier regions.

Changed files:

- `agent/tools_v2/render_tool.py`: extended `RenderDocument` to accept existing PNG/JPG/WebP/BMP images and optional `regions` crop boxes. Region crops are zoomed and returned as image paths for automatic feedback into the next model turn.
- `tests/unit/test_render_tool.py`: added coverage for image-file crop magnifier and PDF-page crop magnifier.
- `requirements.txt`: added explicit `pillow` dependency.
- `agent/ui/server.py`: prompt now tells the model to use `RenderDocument` regions as a magnifier for screenshots/generated images/local visual details.

Replay:

- `tests/p3_vision_loop_results/2026-04-22-magnifier-feedback/`
- Result: passed. The second model call received both the original image and the magnified crop as image feedback.

Verification:

- `pytest tests/unit/test_render_tool.py tests/unit/test_agent_loop.py tests/unit/test_adapter_conversion.py tests/unit/test_agent_chat_v2_contract.py -q`
- Result: `41 passed`

P3 minimal infrastructure status:

- Complete.
- Browser verification, document rendering, image magnifier crops, and image-block feedback are implemented with two tools.
- Office-specific high-fidelity Excel COM belongs to P4 Office skill work.
- Generated-image creation/editing belongs to P5, built on this P3 feedback path.
- Sandbox/active-window execution capture belongs to P6.
- Regression scoring belongs to P8.

## 2026-04-22 - Codex - Doubao vs GPT P3 Composite Test

Codex ran the same P3 composite task through `/api/agent_chat_v2` for:

- `doubao-code` / `doubao-seed-2.0-code`
- `gpt-5.4` / `gpt-5.4`

Artifacts:

- `tests/model_comparison_results/2026-04-22-p3-complex/`

Task coverage:

- PDF brief rendering.
- Avatar image magnifier inspection.
- HTML artifact repair.
- Browser `Verify`.
- Independent post-run verification.
- Activity trace export.

Result:

- Both models passed independent Verify.
- Doubao: correct but minimal output; many reasoning/activity deltas.
- GPT: more polished output and faster in this run; recreated the avatar in CSS while preserving the required `badge VX-17` detail.

This confirms the P3 minimal tool path is provider-robust for the tested task. Visual design quality and stricter reference-image fidelity should be moved into P8 rubrics.

## 2026-04-22 - Codex - P4 Excel Minimal Tool Slice

Codex started P4 with a constrained Excel path instead of exposing arbitrary Python/COM scripting.

Principles applied:

- Sandbox boundary: Excel mutation is a structured tool call, not arbitrary shell/Python/COM code.
- Minimal tools: only `ExcelRead` and `ExcelEdit` were added; rendering still reuses `RenderDocument`.
- Progressive disclosure: Excel/Office prompts expose `Read`, `Glob`, `ExcelRead`, `ExcelEdit`, and `RenderDocument` only. They do not expose `Bash`, `Write`, or text-file `Edit` by default.

Changed files:

- `agent/tools_v2/excel_tool.py`: added `ExcelRead` and `ExcelEdit`.
- `agent/ui/server.py`: added an `office_excel` capability scope with the narrow Excel tool surface.
- `agent/core/loop.py`: records Excel read/edit evidence for delivery guards.
- `agent/core/hooks.py`: final guard accepts `ExcelEdit` as artifact evidence and `ExcelRead`/`RenderDocument` as verification evidence.
- `tests/unit/test_excel_tool.py`: covers Excel read, read-before-edit, scoped edits, large-range rejection, and protocol flags.
- `tests/unit/test_agent_chat_v2_contract.py`: covers Excel progressive disclosure.

Current Excel behavior:

- `ExcelEdit` requires `ExcelRead` on the same workbook in the current AgentLoop run.
- Every edit operation requires an explicit sheet and cell/range/index.
- Large range edits are rejected unless `allow_large_scope=true`.
- A backup copy is created by default before mutation.
- Supported first-slice operations: `set_cell`, `set_range_style`, `set_number_format`, `insert_rows`, `delete_rows`, `set_column_width`, and `set_row_height`.

Verification:

- `.venv\Scripts\python.exe -m pytest tests/unit/test_excel_tool.py -q`
- Result: `5 passed`
- `.venv\Scripts\python.exe -m pytest tests/unit/test_agent_chat_v2_contract.py -q`
- Result: `9 passed`
- `.venv\Scripts\python.exe -m pytest tests/unit -q`
- Result: `222 passed, 5 skipped`
