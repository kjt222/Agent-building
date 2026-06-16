"""P18-C — task 12: a trivially small edit under a low iteration cap.

RECONSTRUCTED INTERPRETATION (see task_09 header). The original P18-C task 12
was the tier's "doom-loop detection" case (the base.py contract note calls it
out by name). Kept in that spirit here: the actual work is tiny — replace a
single placeholder token in one small file — but ``MAX_ITERATIONS`` is capped
low. A focused agent finishes in 2-3 tool calls; an agent that thrashes
(re-reading, re-planning, looping) burns the cap and fails. So this scores
*efficiency / loop-avoidance*, not capability.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

PROMPT = (
    "把这个文件里的占位符 __VERSION__ 替换成 1.0:{path}\n"
    "只改这一个地方,改完就结束,不要做别的。"
)
MODE = "full-access"
TIMEOUT_S = 180.0
MAX_ITERATIONS = 6  # low on purpose: a doom-looping agent will blow this cap.

_BEFORE = 'app_name = "demo"\nversion = "__VERSION__"\nenabled = true\n'
_AFTER = 'app_name = "demo"\nversion = "1.0"\nenabled = true\n'


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t12_"))
    path = workdir / "config.toml"
    path.write_text(_BEFORE, encoding="utf-8")
    state: dict[str, Any] = {"workdir": str(workdir), "path": str(path)}
    state["_prompt"] = PROMPT.format(path=str(path))
    return state


def verify(outcome, state) -> tuple[bool, str]:
    path = Path(state["path"])
    if not path.is_file():
        return False, "target file vanished"
    got = path.read_text(encoding="utf-8")
    if "__VERSION__" in got:
        return False, "placeholder still present (likely looped out / never edited)"
    if got != _AFTER:
        return False, f"file changed beyond the single token: {got!r}"
    return True, "single token replaced cleanly within the iteration cap"


def teardown(state) -> None:
    workdir = state.get("workdir")
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)
