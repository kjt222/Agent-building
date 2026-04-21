---
name: log-management
description: Update project logs for this repository with the correct split between experiments.md, channel.md, implementation.md, and daily_log.md. Use when recording experiment results, design decisions, code changes, tests, or day-by-day progress.
argument-hint: [what changed or what run finished]
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash, Edit, Write
---

Use this skill when the task is to record work into repository logs.

## Goals
- Keep logging consistent and minimal.
- Prevent analysis from leaking into the wrong file.
- Keep experiment history reproducible.
- Keep day-level progress visible in `daily_log.md`.

## Required file split
- `experiments.md`
  - parameters
  - raw tables
  - output paths
  - no interpretation
- `channel.md`
  - plans
  - design decisions
  - analysis
  - failure diagnosis
  - comparisons and conclusions
- `implementation.md`
  - code changes
  - files changed
  - tests run
  - acceptance / validation outcome
  - no experiment interpretation unless it directly validates code behavior
- `daily_log.md`
  - day-level chronology
  - concise progress checkpoints
  - what started, what finished, what is blocked

## Required workflow
1. Read the relevant section at the end of each target log file before editing.
2. If logging an experiment:
   - write config and raw metrics to `experiments.md`
   - write interpretation to `channel.md`
   - add a concise dated progress entry to `daily_log.md`
3. If logging a code change:
   - write change summary, files, tests, and results to `implementation.md`
   - add a concise dated progress entry to `daily_log.md`
4. Preserve the user's preferred structure and terminology already used in the repo.
5. Never duplicate the same analysis across multiple log files.

## Automation notes
- Repository automation entry points live in `automation/experiment_ops/`.
- If a task is about post-run analysis or log updates, prefer wiring the automation there instead of adding more one-off shell wrappers.

## Additional resources
- For the repo-specific logging policy, see [references/repo-logging-policy.md](references/repo-logging-policy.md).
