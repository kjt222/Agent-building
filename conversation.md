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

