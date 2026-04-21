# UI Conversation Task Review

Runner: Codex
Date: 2026-04-22
Server: `http://127.0.0.1:8686/`
Profile: `doubao-code`
Path: real browser UI, not `TestClient`

## Why This Run Exists

Earlier smoke tests used backend calls, so they did not create visible conversations in the left sidebar. This run drives the same UI path as a user:

1. Open the web UI.
2. Click `New chat`.
3. Switch execution mode to `Auto`.
4. Send the task through `/api/agent_chat_v2`.
5. Wait for the stream to finish.
6. Save the visible conversation text and screenshot.

## Tasks

| Task | Purpose | Result | Evidence |
| --- | --- | --- | --- |
| `01_metadata_direct` | Direct runtime metadata answer without tools | Passed | `01_metadata_direct.txt`, `01_metadata_direct.png` |
| `02_repo_read` | Repo inspection with file tools | Passed, 5 tool calls | `02_repo_read.txt`, `02_repo_read.png` |
| `03_snake_fix_verify` | SWE-style bugfix plus browser verification | Passed, 4 tool calls | `03_snake_fix_verify.txt`, `03_snake_fix_verify.png` |
| `04_frontend_build_verify` | Single-file frontend generation plus browser verification | Passed, 6 tool calls | `04_frontend_build_verify.txt`, `04_frontend_build_verify.png`, `claude_home_replica.html`, `screenshot.png` |

## Observations

- The conversations are visible in the left sidebar when the test uses the frontend UI path.
- `AgentLoop v2` is the active path; no Stable/V2 switch is needed for these tests.
- The direct metadata task correctly answered from injected runtime/session metadata and did not call tools.
- The repo inspection task used tools before answering, which confirms the progressive tool exposure path is active for file-oriented tasks.
- The snake task performed the expected read/edit/verify flow and fixed `bad_snake_fixture.html`.
- The frontend generation task wrote a static HTML artifact, read it back, and ran `Verify`.

## Remaining UX Issues

- The saved visible conversation text shows the tool-call count, but not every tool input/result detail unless the Activity panel is expanded in the UI.
- The model still narrates intermediate steps in the assistant message. This is a prompt/UX refinement issue, not a tool-routing failure.
- One earlier RAG UI attempt created a conversation with only the user message because it was launched before the stricter Auto-mode test harness. It was not counted as a pass in this review.

