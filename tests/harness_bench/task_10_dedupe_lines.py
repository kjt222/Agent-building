"""P18-C — task 10: de-duplicate lines in place, preserving first-seen order.

RECONSTRUCTED INTERPRETATION (see task_09 header for why the original P18-C
spec is unavailable). A small act→verify file-editing task: collapse duplicate
lines in a text file, keeping the first occurrence of each and the original
order. Tests that the agent makes a precise, order-preserving edit rather than
sorting or shuffling.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

PROMPT = (
    "这个文件里有重复的行:{path}\n"
    "帮我去掉重复行,只保留每行第一次出现的位置,其余重复的删掉,"
    "其它行的先后顺序保持不变。"
)
MODE = "full-access"
TIMEOUT_S = 240.0

# Input has duplicates; expected output keeps first occurrence order.
_INPUT_LINES = [
    "alpha",
    "beta",
    "alpha",
    "gamma",
    "beta",
    "delta",
    "alpha",
]
_EXPECTED_LINES = ["alpha", "beta", "gamma", "delta"]


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t10_"))
    path = workdir / "lines.txt"
    path.write_text("\n".join(_INPUT_LINES) + "\n", encoding="utf-8")
    state: dict[str, Any] = {"workdir": str(workdir), "path": str(path)}
    state["_prompt"] = PROMPT.format(path=str(path))
    return state


def verify(outcome, state) -> tuple[bool, str]:
    path = Path(state["path"])
    if not path.is_file():
        return False, "target file vanished"
    got = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if got == _EXPECTED_LINES:
        return True, "duplicates removed, first-seen order preserved"
    if sorted(got) == sorted(_EXPECTED_LINES):
        return False, f"right set but wrong order: {got}"
    return False, f"unexpected result: {got}"


def teardown(state) -> None:
    workdir = state.get("workdir")
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)
