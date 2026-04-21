# Repo Analysis Conventions

Use these conventions for RL-reaction-path.

## Primary sources
- `results.json`: phase summary
- `training_log.jsonl`: per-rollout metrics
- `offline/postprocess_summary.json`: decompose/NEB aggregate stats
- `offline/decompose_results.json`: edge-level decompose results
- `offline/neb_ci_results.jsonl`: edge-level CI-NEB results

## Logging split
- `experiments.md`: parameters and raw results only
- `channel.md`: analysis, interpretation, caveats
- `implementation.md`: code changes, tests, acceptance only

## Current experiment interpretation rules
- `decompose` classification is the main source for transition quality.
- CI-NEB values are useful for ranking and coarse path quality checks.
- Do not treat coarse or model-level barriers as final physical barriers without saying so explicitly.
- If most observed transitions are `composite`, say the policy is mostly producing jump transitions rather than clean local steps.
