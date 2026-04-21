# P2 Agent Behavior Replay | 2026-04-22

Recorded by Codex.

## Scope

These replay tests validate P2 at the framework/tool-contract layer without
calling a live model. They use `MockAdapter` to reproduce the important agent
failure paths deterministically.

## Scenarios

| Scenario | Expected behavior | Test |
| --- | --- | --- |
| Edit existing file without reading it first | First `Edit` fails with `Read it first`; model then calls `Read`, retries exact `Edit`, and final delivery is accepted because edit evidence exists. | `tests/unit/test_p2_agent_replay.py::test_replay_edit_failure_then_read_and_exact_edit` |
| False artifact delivery | Assistant first says it created the requested file without tool evidence; Final Guard injects a delivery-contract nudge; model then calls `Write`; final delivery is accepted. | `tests/unit/test_p2_agent_replay.py::test_replay_final_guard_forces_write_after_false_delivery` |
| Dangerous Bash mutation | `git push` is blocked by the Bash policy because only read-only git subcommands are allowed in P2. | `tests/unit/test_p2_agent_replay.py::test_replay_bash_blocks_dangerous_git_mutation` |

## Result

P2 replay passed locally as part of:

```text
.venv\Scripts\python.exe -m pytest tests/unit/test_primitives_contract.py tests/unit/test_p2_agent_replay.py tests/unit/test_hooks.py tests/unit/test_control_tools.py tests/unit/test_agent_loop.py -q
```

This verifies the P2 contract for tool protocol flags, read-before-edit/write,
exact Edit semantics, Bash allowlist/danger blocks, and Final Guard evidence
enforcement.

## Remaining Non-P2 Limits

These tests do not verify visual or semantic quality of generated artifacts.
That belongs to P3 (`Verify` tool, render/screenshot pipeline, image feedback)
and P8 regression grading.
