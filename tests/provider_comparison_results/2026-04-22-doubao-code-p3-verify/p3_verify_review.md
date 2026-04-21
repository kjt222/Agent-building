# P3 Verify Smoke | Doubao Code | 2026-04-22

Recorded by Codex.

## Scope

This smoke verifies the first P3 slice: the agent can use a framework-provided
browser verification tool instead of only reading source code.

## Runtime

- Profile: `doubao-code`
- Provider: `openai_compat`
- Model: `doubao-seed-2.0-code`
- Endpoint: `/api/agent_chat_v2`

## Task

Create a tiny HTML snake-style demo with:

- `#game-over` initially hidden.
- A `canvas`.
- `ArrowRight` should not immediately reveal game over.
- Use `Verify` with browser assertions after writing.

## Result

- The model first tried blocked Bash directory commands.
- It recovered by using `Write`.
- It then called `Verify`.
- `Verify` returned `"ok": true`.
- Screenshot: `verify_screenshot.png`
- Event trace: `events.jsonl`

## Why This Matters

The earlier Doubao snake test only read source code and missed a runtime
game-over bug. This smoke shows the P3 direction works: browser/DOM facts can be
returned as tool evidence and used by the model loop.

This is still a minimal P3 slice. It does not yet feed screenshots back as
image blocks for VLM inspection, and it does not cover Office/PDF/XLSX render
pipelines.
