# Conversation Plan

## Current Status - 2026-04-22

Runner: Codex

P0 is complete for the originally reported sidebar issue: new chats now appear in the conversation list.

P1 is functionally in use for the main UI path:

- The main UI sends chat through `/api/agent_chat_v2`.
- Text streaming and Activity events are visible.
- Session/runtime metadata is injected into the model context.
- Memory/context compaction hooks are wired on the v2 path.
- Multimodal image input is accepted by the v2 request path, subject to provider support.
- Provider switching has been exercised with the Doubao OpenAI-compatible profile.

P1 still has follow-up quality work:

- Plain saved conversation text does not include expanded tool input/result details.
- Some model narration should be reduced so tool work reads more like Claude Code.
- Knowledge/RAG UI testing needs a clean rerun after the Auto-mode harness fix.

P2 core is complete:

- Bash policy and command result structure are in place.
- Read-before-write and final-delivery guard behavior are covered by tests.
- Destructive command handling should be finalized with P6 sandbox policy instead of overfitting local allow/deny logic.

P3 has started:

- Browser `Verify` exists and is exposed to AgentLoop v2 for HTML/CSS/JS/UI artifacts.
- UI-driven tests confirmed `Verify` can catch and validate browser state.

P3 remaining scope:

- Render tools for Office and generated images.
- Message builder support for feeding rendered screenshots/image blocks into the next turn.
- A model-controlled self-review loop that decides when to render and inspect artifacts.

Next recommended step:

1. Rerun the Knowledge/RAG UI case with the corrected Auto-mode harness.
2. Tighten Activity/tool detail persistence so complete tool paths are reviewable after the run.
3. Continue P3 by adding render outputs and image-block feedback into AgentLoop turns.

## P1 Closeout Update - 2026-04-22

Runner: Codex

P1 Activity trace persistence/export is now implemented.

What changed:

- `/api/agent_chat_v2` stores real UI turn traces when a `conversation_id` is present.
- Stored trace includes Activity events, token events, done payload, assistant text, tool manifest, capability scope, provider/model metadata, system prompt hash, and AgentLoop JSONL records.
- Trace lookup/export endpoints are available under `/api/conversations/{conv_id}/activity_traces`.

P1 status:

- P1 is now closed for the current v2 foundation scope.
- Remaining polish is UX-level presentation of trace details in the frontend, not backend trace availability.

P3 status:

- P3 is not done.
- The browser `Verify` tool is complete as the first P3 slice.
- The rest of P3 still needs render/screenshot tools, Office/image artifact handling, image-block feedback into the next model turn, and a self-review loop that can decide whether to re-render and revise.

Next recommended step:

1. Add a frontend "export trace" affordance or trace drawer backed by the new endpoints.
2. Continue P3 with render outputs plus image-block feedback.

## P3 Progress Update - 2026-04-22

Runner: Codex

P3 has advanced but is still not complete.

Completed in this slice:

- `RenderDocument` tool added to AgentLoop v2 full toolset.
- PDF pages render directly to PNG with PyMuPDF.
- DOCX/XLSX/PPTX can render through LibreOffice headless when LibreOffice is installed.
- Tool results that include `screenshot_path`, `rendered_image_path`, or image base64 are automatically attached as `ImageBlock`s to the next model turn.
- OpenAI Chat Completions and Responses adapter conversions both support function/tool output followed by image feedback.
- UI Activity emits an `image_feedback` event when the loop attaches rendered images.

P3 still open:

- Excel COM rendering path for higher fidelity Windows Office screenshots.
- Script/active-window screenshot capture.
- Generated-image tool plus img2img/inpainting loop.
- Self-review policy that decides when to run render/Verify again.
- Regression tasks that prove the model actually uses visual feedback to correct artifacts.

P3 replay result:

- `tests/p3_vision_loop_results/2026-04-22-render-feedback/summary.json`
- Result: passed.
- The framework successfully rendered a PDF page and attached the PNG as an `ImageBlock` to the next model call.

Image-generation direction:

- P5 should be designed as an iterative image production loop, not only a generate-image endpoint.
- Required capabilities: full-image review, crop/zoom magnifier review, local inpainting/detail fixes, image comparison, identity/style reference storage, and series-level consistency profiles.
- The main technical dependency is already started in P3: generated or rendered images can be returned into the next model turn as image blocks.

## P3 Minimal Completion - 2026-04-22

Runner: Codex

P3 minimal infrastructure is now complete under the Claude Code-style "few tools, strong contracts" principle.

Final P3 tool surface:

- `Verify`: browser/HTML/URL verification and screenshot capture.
- `RenderDocument`: PDF/Office document rendering, existing image inspection, and crop/zoom magnifier regions.

Completed:

- Browser artifact verification.
- PDF rendering to PNG.
- LibreOffice-backed Office-to-PDF-to-PNG path when LibreOffice is installed.
- Existing image passthrough.
- Region crop magnifier for screenshots/generated images/document renders.
- Automatic image feedback into the next model turn.
- Activity trace for image feedback.
- Unit coverage and two replay artifacts:
  - `tests/p3_vision_loop_results/2026-04-22-render-feedback/`
  - `tests/p3_vision_loop_results/2026-04-22-magnifier-feedback/`

Moved out of P3:

- Excel COM high-fidelity rendering: P4 Office skill.
- Image generation/inpainting/identity consistency: P5.
- Script execution screenshot and sandboxed capture: P6.
- Provider live vision evals and regression scoring: P8.

Next phase:

- Start P5 only after defining image asset identity/style persistence and local-edit workflow, otherwise generated characters will drift between turns.

## Model Comparison - 2026-04-22

Runner: Codex

Composite P3 task tested on both `doubao-code` and `gpt-5.4`.

Location:

- `tests/model_comparison_results/2026-04-22-p3-complex/`

Outcome:

- Both models used the visual/tool path and passed independent browser verification.
- Doubao output was correct and literal but visually sparse.
- GPT output was more polished and completed faster in this run.
- The framework held up across providers: render feedback, edit/write, and Verify all worked.

Follow-up:

- Add P8 visual-quality rubrics, because current Verify assertions prove functional correctness but do not score design quality or reference-image fidelity deeply enough.

## P4 Excel Start - 2026-04-22

Runner: Codex

P4 has started with the Excel slice.

Implemented now:

- `ExcelRead`: inspect workbook/sheet/range values, formulas, styles, merged ranges, filters, and used ranges.
- `ExcelEdit`: apply scoped structured edits after `ExcelRead`.
- Excel-specific progressive disclosure: Excel/Office prompts expose only `Read`, `Glob`, `ExcelRead`, `ExcelEdit`, and `RenderDocument`.
- Final guard evidence now recognizes Excel edits and Excel/render verification evidence.

Guardrails:

- No arbitrary Python/COM script execution for workbook edits in this slice.
- `ExcelEdit` requires the workbook to be inspected first in the same AgentLoop run.
- Every edit op must specify an explicit sheet and target cell/range/index.
- Broad range edits are blocked unless explicitly requested through `allow_large_scope=true`.
- Backups are created before mutation by default.

Why this shape:

- It follows the Claude Code-style Read/Edit contract while keeping tool count small.
- `RenderDocument` remains the visual verification tool, so P4 does not add a separate Excel screenshot tool.
- High-fidelity Windows Excel COM rendering/editing remains a later P4 expansion or MCP/server boundary, especially for plugin-dependent workflows such as EndNote.

Verification so far:

- `tests/unit/test_excel_tool.py`: passed.
- `tests/unit/test_agent_chat_v2_contract.py`: passed.
- Full unit suite: `222 passed, 5 skipped`.

Next P4 work:

1. Add a real Excel fixture task that edits values and formatting, renders it with `RenderDocument`, and stores the Activity trace under `tests/`.
2. Expand supported structured ops only when a real task needs them.
3. Keep EndNote/reference-manager integration as MCP-oriented work unless a local deterministic file-based citation flow is enough.

## P4 Excel Complex Template Update - 2026-04-23

Runner: Codex

P4 Excel has moved beyond the minimal value/style edit scenario into a template-formatting scenario.

Implemented in this slice:

- `ExcelEdit.copy_range_style`: copy style from an explicit source range to an explicit target range.
- `ExcelEdit.merge_cells` / `ExcelEdit.unmerge_cells`: explicit merge control.
- `ExcelRead` now reports row heights and column widths for inspected ranges.

Guardrails kept:

- Still only two Excel tools: `ExcelRead` and `ExcelEdit`.
- Style copy requires source and target ranges with the same shape.
- Edits still require prior `ExcelRead`.
- Large target ranges still require `allow_large_scope=true`.
- No arbitrary Python/COM/script editing.

Real UI validation:

- Location: `tests/p4_excel_validation/2026-04-23-complex-template/`
- Conversation ID: `conv_20260423_003948_f4051b`
- The agent copied title/header template formatting from `Template` to `Report`, merged the title row, set delta formulas, rendered the workbook, and visually checked the result.
- Tool path: `ExcelRead`, `ExcelRead`, `ExcelEdit`, `RenderDocument`.

Independent check:

- Template title/header style transfer succeeded.
- Row height/column width matched the template.
- Formulas were set in `Report!D4:D6`.
- `Notes` sheet was unchanged.

Verification:

- Full unit suite: `224 passed, 5 skipped`.

Next P4 work:

1. Add a task where the model must preserve an existing workbook layout while modifying only one local section.
2. Add chart/image/table-style operations only if that task forces the need.
3. Decide whether high-fidelity Excel COM should stay in-process or move behind an MCP/server boundary.
