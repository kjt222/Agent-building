# P1 Agent Smoke Results

Created by Codex on 2026-04-21.

## Scope

These artifacts test the current `/api/agent_chat_v2` behavior with real model calls. They are manual smoke results, not pytest assertions.

## Tests

### Snake Code Generation

Prompted the agent to generate a complete self-contained Snake game.

Artifacts:

- `snake_raw_sse.txt`
- `snake_answer.md`
- `snake_generated.html`
- `snake_activities.json`
- `snake_done.json`
- `snake_browser_smoke.json`
- `snake_initial.png`
- `snake_after_controls.png`
- `snake_logic_review.md`

Result:

- Browser smoke passed.
- Main game logic is playable.
- One Snake rules edge-case bug found: self-collision is checked before removing the non-eating tail, so moving into the current tail cell can be falsely treated as collision.

### Claude Screenshot Frontend Replication

Prompted the agent to generate a single-file HTML/CSS replication of the provided Claude home screenshot.

Artifacts:

- `claude_clone_prompt.txt`
- `claude_clone_raw_sse.txt`
- `claude_clone_answer.md`
- `claude_clone_generated.html`
- `claude_clone_activities.json`
- `claude_clone_done.json`
- `claude_clone_browser_smoke.json`
- `claude_clone_render.png`
- `claude_clone_review.md`

Result:

- Browser smoke passed.
- Layout is a reasonable first-pass static replication.
- The original attached screenshot was not available to the local harness as a file/base64 payload, so this was description-based rather than a true image-input test.
