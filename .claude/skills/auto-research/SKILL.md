---
name: auto-research
description: "Run budget-limited autonomous research loops: propose hypotheses, edit code, launch experiments, analyze artifacts, and decide next steps, while always updating repository logs. Use when the goal is to improve results through iterative experimentation without manual step-by-step supervision."
argument-hint: "[research goal and budget]"
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent
---

Use this skill when the user wants Claude Code to autonomously drive research in this repository.

This is not a pure tuning skill. It may:
- change hyperparameters
- change reward design
- change model architecture
- add new backends or diagnostics
- launch experiments
- analyze results

## Execution model: event-driven persistent loop

Claude Code is turn-based — user messages interrupt the current turn.
To maintain a continuous research loop despite this, use an **event-driven chain**:

1. **Launch experiment via `run_in_background`** (Bash tool with `run_in_background: true`)
2. **Tell user** what's running (1-2 lines: hypothesis, EXP tag, estimated time)
3. **While waiting**: respond to user messages normally. The background experiment keeps running.
4. **On completion notification**: analyze results, update logs, launch next experiment in background
5. **Repeat** until budget exhausted or stopping rule triggered

### State persistence

All loop state MUST be persisted to disk so context compression cannot break the chain.

**State file**: `.claude/research_state.json`
```json
{
  "loop_id": "research_20260310_preference",
  "goal": "...",
  "primary_metric": "...",
  "budget": { "max_rounds": 10, "remaining": 7, "started_at": "..." },
  "current_round": 3,
  "phase": "awaiting_result",
  "current_experiment": {
    "tag": "EXP57",
    "pid": 12345,
    "output_dir": "runs/output/EXP57_...",
    "launched_at": "2026-03-10T14:00:00"
  },
  "history": [
    { "tag": "EXP56", "result_summary": "...", "decision": "..." }
  ]
}
```

**Rules**:
- Write state file BEFORE launching each experiment
- Update state file AFTER analyzing each experiment
- On every turn (including user-initiated), read state file first to recover context
- If `phase == "awaiting_result"`, check if the experiment output exists before doing anything else

### Handling user interruptions

When the user sends a message during an active research loop:
1. Read `.claude/research_state.json` to recover loop context
2. Answer the user's question
3. Check if the background experiment has completed (look for output files)
4. If completed: continue the loop (analyze → log → next experiment)
5. If still running: tell the user the experiment is still in progress
6. NEVER abandon the loop just because the user asked a question

### Fallback: long experiments (>10 min)

For experiments exceeding Bash timeout (10 min):
1. Write a launcher shell script that:
   - Runs the experiment
   - Writes a `.done` sentinel file on completion
   - Captures exit code and final metrics to a `.result.json`
2. Launch the script with `nohup` via `run_in_background`
3. On each subsequent turn, check for the sentinel file
4. This ensures the experiment survives even if Claude Code disconnects

### Resuming after session break (quota exhaustion, disconnect, new conversation)

When the user says "继续", "continue", or invokes `/auto-research continue` in a new session:
1. Read `.claude/research_state.json` — this is the single source of truth
2. Read `channel.md` tail (~50 lines) and `experiments.md` tail (~30 lines) for recent context
3. Determine where the loop stopped:
   - `phase: "running"` + experiment output exists → experiment finished while offline, analyze it
   - `phase: "running"` + no output → experiment was killed, re-launch it
   - `phase: "analyzed"` → ready to start next round
   - `phase: "awaiting_result"` + sentinel `.done` exists → same as "running" + output exists
4. Resume the loop from that point. Do NOT re-run completed experiments.
5. Preserve the original budget: use `remaining` rounds, not `max_rounds`.
6. Briefly tell the user what round you're resuming from and what the current hypothesis is.

This ensures the loop survives:
- Quota exhaustion (5h/day or weekly limit)
- Terminal close / SSH disconnect
- Claude Code restart
- Account switch

### Fallback: heartbeat via /loop

If the notification chain breaks (e.g., context was fully reset), the user can restart with:
```
/loop 5m /auto-research continue
```
Each invocation reads state from disk and picks up where it left off.
The `continue` argument means: do not restart, read `.claude/research_state.json` and resume.

## Hard constraints

- A concrete budget must be stated before starting the autonomous loop.
- Logging is mandatory on every completed iteration.
- Code changes must integrate with the existing repo structure; do not scatter one-off logic across unrelated files.
- Do not skip artifact analysis between iterations.
- Do not run unlimited experiment trees in parallel.
- GPU is a hard prerequisite for experiments in this repo unless the user explicitly overrides it.
- Never silently fall back to CPU for training, pretraining, diagnostics, or sampling experiments.
- If CUDA/NVML/GPU allocation fails or the GPU becomes unavailable mid-loop:
  1. stop launching new experiments immediately
  2. treat it as a hardware/runtime blocker, not a research result
  3. report the hardware issue to the user and wait rather than continuing on CPU

## Budget

Minimum required budget fields:
- max experiment rounds
- max wall time per round or total wall time
- max GPU-hours or an explicit experiment-count proxy

Required budget format:
```text
Budget:
- max_rounds: <int>
- max_wall_time_per_round: <duration>    # optional if total wall time is given
- max_total_wall_time: <duration>        # optional if per-round wall time is given
- max_gpu_hours: <float>                 # preferred
  or
- max_experiments: <int>                 # fallback proxy when GPU-hours are not given
```

If the user has not provided a budget:
1. stop the autonomous loop
2. ask only for the missing budget information

## Research objective

- Optimize the user-specified primary metric.
- If the user did not specify one, define a primary metric before starting and record it in `channel.md`.
- Keep auxiliary metrics separate from the primary metric.

Determine the primary metric by reading the current `channel.md` and `experiments.md` context — do not hardcode a specific metric. The metric hierarchy may evolve as the research progresses.

Do not optimize a convenience metric just because it is easy to read from logs.

## Required loop for each round

1. **Check coordination (MANDATORY before ANY action)**:
   a. Read the **last 200 lines of `channel.md`** to check:
      - What Codex is currently doing (look for `### Codex —` entries)
      - Which files Codex owns (do NOT touch them)
      - GPU usage by Codex experiments
      - Any messages or requests from Codex to Claude Code
   b. Check `nvidia-smi` for GPU memory/process status
   c. If conflicts exist, resolve before proceeding
   d. Update your own coordination entry in `channel.md` with `### Claude Code —` heading
2. **Recover state**: Read `.claude/research_state.json`. If missing, this is round 1.
3. **Read current artifacts and code** relevant to the hypothesis.
4. **State the current hypothesis** in `channel.md`.
5. **Decide the smallest high-information intervention.**
6. **Implement code changes** if needed.
7. **Validate code paths** before launching runs.
8. **Verify GPU availability** (`nvidia-smi`) before launching any experiment.
9. **Tag experiment with `CC_` prefix** (e.g., `CC_EXP87_ucb_basin`) to distinguish from Codex experiments.
10. **Write state file** with `phase: "running"`, experiment tag, output dir.
11. **Launch experiment** via `run_in_background`. For >10min experiments, use the nohup launcher pattern.
12. **Notify user** (1-2 lines: what's running, expected duration).
13. **On completion notification** (or sentinel file detected):
    a. Analyze artifacts directly from disk using `data-analysis` skill conventions.
    b. Update logs:
       - `experiments.md`: settings and raw results
       - `channel.md`: interpretation, diagnosis, next-step logic
       - `implementation.md`: code changes, tests, validation
    c. Update state file with `phase: "analyzed"`, result summary, decision.
14. **Decide whether to continue or stop.** If continuing, go to step 1 (re-check coordination).

## Decision rules

- Continue only if the next round is justified by the artifacts from the previous round.
- Prefer one decisive experiment over many weakly differentiated ones.
- Prefer diagnosis over blind parameter sweeps.
- If a mechanism-level issue is more likely than a hyperparameter issue, investigate mechanism first.
- If the current architecture is likely the bottleneck, it is allowed to redesign it, but preserve repo boundaries and shared interfaces.

## Code integration rules

Reuse existing seams:
- `rl/schema.py`
- `rl/policies/`
- `rl/training/algorithms/`
- `rl/action_space/`
- `rl/pipeline.py`
- `rl/rewards.py`
- `rl/pretrain/`
- `frontends/train_rl.py`

New architectures should land as clear modules or backends, not as ad hoc conditionals spread across the codebase. Keep model design separate from algorithm logic whenever possible.

### Code safety

- **Do not break existing code.** New features must be additive — add new modules, new flags, new branches. Do not modify existing function signatures, default behavior, or return types unless the change is the explicit goal.
- Before modifying a shared module (e.g., `rl/pipeline.py`, `rl/runner.py`), read callers to confirm no downstream breakage.
- If a code change touches >3 files, run a quick smoke test (import check + one short run) before launching the full experiment.

### Mandatory code audit after 3 consecutive failures

If 3 consecutive experiment rounds fail to improve the primary metric:
1. **STOP launching new experiments.**
2. **Run a code audit** before the next round:
   - Re-read all code changed since the last successful round.
   - Check for logic errors: wrong sign, wrong variable, off-by-one, tensor shape mismatch, gradient flow issues.
   - Check for silent failures: NaN propagation, empty tensors, vacuous loss terms.
   - Check for config mismatches: is the experiment actually running the intended code path?
3. **Log the audit result** in `channel.md` under a "Code Audit (round N)" heading.
4. **Only resume experiments** after the audit concludes — either with a fix or with explicit confirmation that the code is correct and the bottleneck is elsewhere.
5. Record in `research_state.json`: `"consecutive_failures": N`, reset to 0 on any improvement.

This rule exists because: in this project, logic bugs have historically caused more wasted experiments than bad hyperparameters.

## Stopping rules

- Stop when the budget is exhausted.
- Stop when the primary metric meaningfully improves and the next step is implementation-focused rather than experiment-focused.
- Stop when multiple consecutive rounds fail to improve the primary metric and no stronger hypothesis remains.
- Stop when the result points to a prerequisite task, such as pretraining, data generation, or a representation redesign.
- Stop immediately and report if GPU execution is unavailable and the task would otherwise require a CPU fallback.
- Do not stop merely to ask for confirmation, provide an interim summary, or hand control back early. Keep going until one of the stopping rules or the task goal is reached.

## Mandatory post-experiment skills (non-negotiable)

After EVERY completed experiment round, you MUST invoke both companion skills in order.
Skipping either one is a hard error — treat it like a missing commit in a deploy pipeline.

1. **`data-analysis`** — Analyze the experiment output from disk.
   - Follow the workflow in `.claude/skills/data-analysis/SKILL.md`.
   - Read `results.json`, `training_log.jsonl`, offline outputs as available.
   - Compute metrics from raw files, not from memory or summaries.
   - Output the three-section report (trajectory quality, transition quality, learning signal).

2. **`log-management`** — Write results and analysis to the correct log files.
   - Follow the workflow in `.claude/skills/log-management/SKILL.md`.
   - `experiments.md`: config, raw metrics, output paths — NO interpretation.
   - `channel.md`: hypothesis, analysis, diagnosis, next-step decision.
   - `implementation.md`: any code changes made this round.
   - `daily_log.md`: concise dated entry.
   - Never duplicate analysis across files.

If an experiment crashes or produces no output, still invoke `log-management` to record the failure, crash log, and diagnosis.

## Default research style

- Make one clear move at a time.
- Prefer reversible changes.
- Keep experiments comparable unless the hypothesis explicitly requires a regime change.
- Be aggressive about diagnosis, conservative about code sprawl.

## Output style during autonomous runs

- Keep user updates short.
- Say what hypothesis is being tested, what is being changed, and what would count as success or failure.
- Use Chinese for all user-facing communication (per user preference).

## Environment

- Python: `/home/kjt/miniforge3/envs/mace/bin/python` (MACE env)
- Project root: `/home/kjt/projects/RL-reaction-path`
- Always set `PYTHONPATH=$PROJECT` and `cd $PROJECT` before launching experiments.
- Main entry point: `frontends/train_rl.py`
- BC pretrain entry: `rl/pretrain/structured_bc.py`
- Structure configs: `configs/structures/`
- MACE manifest: `models/MACE-MH-1/OC22-finetune-head_only/artifacts/manifest.json`
