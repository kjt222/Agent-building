# P3 Render Feedback Replay

Runner: Codex
Date: 2026-04-22

## Task

Render a one-page PDF, feed the rendered PNG back into the next model turn, and confirm that the next model call receives an `ImageBlock`.

This replay uses a deterministic mock adapter rather than a remote provider so the test isolates framework behavior:

1. First model turn calls `RenderDocument`.
2. `RenderDocument` renders `layout_fixture.pdf` page 1 to PNG.
3. `AgentLoop` extracts `rendered_image_path` from the tool result.
4. `AgentLoop` attaches the PNG as an `ImageBlock` to the next user/tool-result message.
5. Second model turn confirms `saw-rendered-image=true`.

## Result

Passed.

Evidence:

- `summary.json`
- `transcript.txt`
- `loop_trace.jsonl`
- `layout_fixture.pdf`
- `renders/layout_fixture_page_1_1776842651838.png`

## Why This Matters

This proves the P3 foundation is no longer only text-side verification. Rendered visual artifacts can now be carried into the next model turn. That is the shared dependency for Office review, generated-image review, screenshot review, and later self-correction loops.

## Remaining Gap

This test proves image transport into the next model turn. It does not prove that a specific remote provider reasons well over the image. That requires provider-specific live evals with GPT/Claude/Gemini/Doubao vision models.

