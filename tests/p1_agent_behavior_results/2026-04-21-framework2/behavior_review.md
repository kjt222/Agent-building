# P1 Agent Behavior Review - Framework-Level Self-Check

Recorder: Codex
Date: 2026-04-21

## Method

The two tasks were rerun with neutral user prompts after moving the self-check requirement into the framework-level system prompt. The prompts did not explicitly say "self-check", "review", or "fix after writing".

Framework changes used for this run:

- Artifact tasks expose file/code tools progressively.
- Direct questions expose no tools.
- Knowledge tasks expose knowledge/read-only tools.
- System prompt says artifact tasks must write the exact requested target, verify after writing, and avoid substituting older similar files.
- Stop hook catches common "I can continue if you want" deferrals and nudges the model to execute.

## Task 1 - Snake HTML

Target:

`tests/p1_agent_behavior_results/2026-04-21-framework2/snake_neutral.html`

Artifacts:

- `snake_neutral.html`
- `snake_neutral_raw_sse.txt`
- `snake_neutral_events.json`
- `snake_neutral_summary.json`
- `snake_neutral_answer.md`
- `snake_neutral_render.png`
- `snake_neutral_browser_smoke.json`

Observed behavior:

- The agent created the exact target file.
- Tool path was `Write -> Read -> Read`.
- Browser smoke passed: page loaded, `canvas` exists, no console/page errors, Space starts the game.
- The agent did not run browser verification itself; Codex ran Playwright after the task.
- The agent did not call `Edit`, so no self-repair happened.

Logic issue still present:

- In `update()`, self-collision checks `snake.some(...)` before non-eating movement removes the tail with `snake.pop()`.
- This can falsely treat moving into the current tail cell as collision when the tail would move away in the same tick.

Conclusion:

Partial correction behavior. The framework prompt made the agent write and read back the artifact, but it did not reason deeply enough to catch the real game-logic edge case.

## Task 2 - Claude Screenshot Frontend Clone

Target:

`tests/p1_agent_behavior_results/2026-04-21-framework2/claude_clone_neutral.html`

Artifacts:

- `claude_clone_neutral_raw_sse.txt`
- `claude_clone_neutral_events.json`
- `claude_clone_neutral_summary.json`
- `claude_clone_neutral_answer.md`

Observed behavior:

- The agent did not create the target file.
- Tool path was `Read -> Glob -> Glob -> Glob -> Glob`.
- It treated the task as if an existing HTML file should be found and analyzed.
- It noticed screenshot content, but still asked for a file/path instead of creating the requested clone.
- No rendered page exists for this run.

Conclusion:

Failed. The model did not reliably follow "create target artifact" for an image-to-frontend task, even after framework prompt tightening. This is a current behavior gap and supports moving to P3/P8 style render/verify loops rather than relying on prompt-only self-correction.

## Overall Finding

P1 now has the plumbing needed for v2, but self-correction is not robust as a product behavior:

- Good: exact-file creation improved for text/code artifact tasks after framework prompt changes.
- Weak: validation is mostly read-back, not true execution/render verification.
- Bad: multimodal image-to-artifact task can still derail into "find existing file" behavior.

Next required work:

- P3 `Verify` tool and render/image feedback loop.
- P8 regression tasks that assert artifact existence, browser render, and concrete logic checks.
- Stronger task-state contract in AgentLoop: if user requested an output path, final answer should be blocked or flagged when the file does not exist.
