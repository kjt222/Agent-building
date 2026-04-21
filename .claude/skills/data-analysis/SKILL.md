---
name: data-analysis
description: Analyze RL, basin, transition, and NEB experiment outputs in this repository. Use when comparing experiments, diagnosing learning behavior, summarizing trajectory quality, transition quality, or offline postprocess results.
argument-hint: [experiment-paths or question]
allowed-tools: Read, Grep, Glob, Bash
---

Use this skill when the task is to analyze experiment outputs rather than modify training code.

## Goals
- Compare runs using the repository's actual result files, not memory.
- Separate:
  1. trajectory quality,
  2. transition quality,
  3. learning signal / efficiency.
- Keep raw numbers in `experiments.md` and interpretation in `channel.md`.

## Required workflow
1. Identify the experiment roots and collect these files when present:
   - `results.json`
   - `training_log.jsonl`
   - `offline/postprocess_summary.json`
   - `offline/neb_ci_results.jsonl`
   - `offline/decompose_results.json`
2. Compute metrics from files directly. Do not infer from old summaries if raw files exist.
3. Report results in three sections:
   - trajectory quality
   - transition quality
   - whether the policy is learning, and how efficiently
4. When offline outputs exist, treat `decompose` as the primary transition-quality source. Treat NEB barrier values as coarse unless the run is explicitly a refined CI-NEB workflow.
5. If automation is needed, place reusable code in `automation/experiment_ops/` rather than adding more ad hoc logic to skills.

## Additional resources
- For repository-specific metric and file conventions, see [references/repo-analysis-conventions.md](references/repo-analysis-conventions.md).
