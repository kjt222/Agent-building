# Doubao Code Provider Smoke | 2026-04-22

Recorded by Codex.

## Runtime

- Profile: `doubao-code`
- Provider: `openai_compat`
- Model: `doubao-seed-2.0-code`
- Base URL: `https://ark.cn-beijing.volces.com/api/coding/v3`
- Key storage: keyring ref `doubao-code.llm.openai_compat`

The `/api/agent_chat_v2` metadata path correctly reported:

```text
Profile: doubao-code, Provider: openai_compat, Model: doubao-seed-2.0-code
```

## Test Artifacts

| Case | Artifact | Event trace | Result |
| --- | --- | --- | --- |
| Snake game | `snake_doubao.html` | `snake_events.jsonl` | Created with `Write` and read back with `Read`; initial browser smoke found a real logic bug. |
| Snake fix | `snake_doubao.html` | `snake_fix_events.jsonl` | Doubao read the file, used exact `Edit` twice, and fixed the immediate game-over and tail-collision logic. |
| Claude-style frontend | `claude_home_doubao.html` | `claude_clone_events.jsonl` | Created with `Write` and read back with `Read`; browser smoke passed basic rendering checks. |

## Validation

- `browser_smoke.json`: initial browser smoke for snake and frontend.
- `snake_browser_smoke_after_fix.json`: post-fix snake smoke.
- `snake_render.png`, `snake_render_after_fix.png`, `claude_clone_render.png`: screenshots.

Post-fix snake smoke:

```json
{
  "console_errors": [],
  "initial_game_over_display": "none",
  "after_move_game_over_display": "none"
}
```

Claude frontend smoke:

- No console/page errors.
- Sidebar width: `300px`.
- Composer count: `1`.
- Pill count: `5`.
- No horizontal overflow at `1600x768`.

## Behavior Notes

- Doubao first tried shell directory setup commands (`cd ... && dir`, `if not exist ... mkdir ...`) that P2 Bash policy blocked. It recovered by using `Write`, which is the preferred file creation path.
- Direct adapter calls without session metadata produced a wrong self-identification answer. The real v2 route fixed this because runtime/session metadata is injected into the system prompt.
- Final Guard and tool evidence worked: final delivery had actual `Write`/`Read`/`Edit` traces.
- Framework robustness is partial: it can force tool-backed work and let a model recover from blocked commands, but semantic/gameplay bugs still need external validators. The snake bug was caught by Codex/Playwright smoke and then corrected through a second agent turn. This supports the P3/P8 need for `Verify`/render/replay gates.

## Conclusion

The architecture is portable enough for the Doubao coding provider to run the
same v2 AgentLoop path and produce files through tools. The behavior is broadly
similar to the previous P1/P2 findings: file creation works, read-back happens,
but deep self-correction is not reliable unless the framework supplies concrete
validation feedback.
