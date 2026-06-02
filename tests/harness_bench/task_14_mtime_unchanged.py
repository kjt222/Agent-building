"""Tier D — verifier-sanity test 14: claimed file write but mtime unchanged.

No agent is invoked. setup() creates a target file with a baseline mtime,
records the timestamp, then deliberately does NOT touch the file — simulating
an agent that claims "I updated note.md" without actually writing. The strict
mtime-advance verifier must red-light.

Task passes if the verifier correctly detects the silent no-op.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .verifiers import strict_check_mtime_advanced


NEEDS_AGENT = False               # verifier-sanity only; no agent invocation
PROMPT = ""
MODE = "read-only"
TIMEOUT_S = 5.0


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t14_"))
    target = workdir / "note.md"
    target.write_text("# Original content\n", encoding="utf-8")
    # Pin mtime; force it backwards so any real touch will measurably advance it.
    baseline_ns = target.stat().st_mtime_ns
    past_s = (baseline_ns / 1e9) - 5.0
    os.utime(str(target), (past_s, past_s))
    baseline_ns = target.stat().st_mtime_ns

    # Simulate the silent failure: agent says "done" without writing.
    fake_claim = "I appended your todo to ## Tasks. Saved."
    # We intentionally do NOT modify target.
    return {
        "workdir": str(workdir),
        "target": str(target),
        "baseline_mtime_ns": baseline_ns,
        "fake_assistant_text": fake_claim,
    }


def verify(outcome, state) -> tuple[bool, str]:
    target = Path(state["target"])
    baseline = int(state["baseline_mtime_ns"])
    ok, reason = strict_check_mtime_advanced(target, baseline)
    if ok:
        return False, f"VERIFIER LIED: said OK on un-touched file ({reason})"
    return True, f"verifier correctly red-lighted: {reason}"


def teardown(state) -> None:
    import shutil
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
