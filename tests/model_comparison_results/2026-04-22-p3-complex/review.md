# P3 Complex Model Comparison

Runner: Codex
Date: 2026-04-22

## Task

Run the same P3 composite task against two profiles:

- `doubao-code` / `doubao-seed-2.0-code`
- `gpt-5.4` / `gpt-5.4`

The task required the agent to:

1. Render `brand_brief.pdf`.
2. Inspect `avatar_reference.png` with `RenderDocument` magnifier regions.
3. Repair a broken landing page.
4. Use exact brief copy: primary color `#07564c`, tagline, selling points, CTA.
5. Preserve the avatar badge detail `badge VX-17`.
6. Run `Verify` before final delivery.

## Result

Both models passed the independent verification gate.

| Profile | Time | Tool Results | Image Feedback | Independent Verify | Notes |
| --- | ---: | ---: | ---: | --- | --- |
| `doubao-code` | 74.27s | 6 | 3 | passed | Correct and efficient artifact, but visually plain. Used source avatar image directly. Produced many reasoning/activity deltas. |
| `gpt-5.4` | 50.95s | 6 | 2 | passed | More polished layout and stronger visual design. Recreated the avatar in CSS instead of embedding the source image directly; still preserved `badge VX-17`. |

## Independent Verify Assertions

Both outputs passed:

- no horizontal overflow
- contains `Your calm AI product guide`
- contains `badge VX-17`
- contains `Book a guided demo`
- no console errors

## Artifacts

- Shared fixtures:
  - `brand_brief.pdf`
  - `avatar_reference.png`
  - `broken_landing_template.html`
- Doubao:
  - `doubao-code/landing.html`
  - `doubao-code/independent_verify.png`
  - `doubao-code/events.jsonl`
  - `doubao-code/activity_export.jsonl`
- GPT:
  - `gpt-5.4/landing.html`
  - `gpt-5.4/independent_verify.png`
  - `gpt-5.4/events.jsonl`
  - `gpt-5.4/activity_export.jsonl`

## Assessment

The framework was robust enough for both providers on this P3 task: both used the rendered/visual feedback path, both edited the target artifact, and both passed a separate verification run after completion.

The difference is mostly product quality:

- Doubao followed instructions literally and produced a minimal landing page.
- GPT produced a richer page with stronger hierarchy and more complete product storytelling.

The current assertion gate is functional but not strict enough to score visual quality or whether the reference avatar should be embedded versus recreated. Those belong in P8 evaluation rubrics.

