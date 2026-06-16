"""P18-C — task 11: observe a source tree, count markers, write the result.

RECONSTRUCTED INTERPRETATION (see task_09 header). A pure observe→report task:
walk a small source tree, count lines containing ``TODO`` across all .py files,
and write just the integer to an output file. Exercises Grep/Glob/Read style
investigation plus a precise written artifact — the classic observe/act/verify
shape, minus the (unavailable) desktop-screenshot capability.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

PROMPT = (
    "在这个文件夹下:{workdir}\n"
    "统计所有 .py 文件里包含 'TODO' 的行一共有多少行,"
    "把这个数字(只要数字本身)写到文件 {out}。"
)
MODE = "full-access"
TIMEOUT_S = 240.0

# Files and how many TODO-bearing lines each contributes.
_TREE = {
    "a.py": "x = 1  # TODO fix\nprint(x)\n# TODO later\n",      # 2
    "pkg/b.py": "def f():\n    pass  # TODO implement\n",          # 1
    "pkg/c.py": "clean = True\n",                                  # 0
    "notes.md": "TODO this is markdown, must not count\n",         # 0 (not .py)
}
_EXPECTED_COUNT = 3


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t11_"))
    for rel, content in _TREE.items():
        p = workdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    out = workdir / "todo_count.txt"
    state: dict[str, Any] = {"workdir": str(workdir), "out": str(out)}
    state["_prompt"] = PROMPT.format(workdir=str(workdir), out=str(out))
    return state


def verify(outcome, state) -> tuple[bool, str]:
    out = Path(state["out"])
    if not out.is_file():
        return False, "output file not written"
    raw = out.read_text(encoding="utf-8").strip()
    # Accept the bare number (possibly with trailing prose stripped).
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return False, f"no number in output: {raw!r}"
    if int(digits) == _EXPECTED_COUNT:
        return True, f"correct TODO line count ({_EXPECTED_COUNT})"
    return False, f"wrong count: got {digits}, expected {_EXPECTED_COUNT}"


def teardown(state) -> None:
    workdir = state.get("workdir")
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)
