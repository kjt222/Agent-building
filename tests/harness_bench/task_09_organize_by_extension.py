"""P18-C — task 9: observe a messy folder, organize it, verify (full-access).

RECONSTRUCTED INTERPRETATION. The original P18-C "Desktop observe/act/verify"
spec was lost in the D-drive-format recovery, and this build has no desktop
screenshot/click tools (the original tier used a vision model + screenshots).
Re-scoped here as a local-filesystem observe→act→verify task the agent can
genuinely do with full-access Bash/Read/Write: it must inspect a messy folder,
sort files into per-extension subfolders, and delete a stray temp file —
without losing or corrupting any real file.

The verifier checks the final on-disk state (deterministic), so a "describe
but don't act" answer fails.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

PROMPT = (
    "整理这个文件夹:{workdir}\n"
    "把里面的文件按扩展名分别移动到以扩展名命名的子文件夹里"
    "(例如 .txt -> txt/、.csv -> csv/、.md -> md/),"
    "并删除所有 .tmp 临时文件。不要改动文件内容,不要丢文件。"
)
MODE = "full-access"
TIMEOUT_S = 300.0

_FILES = {
    "notes.txt": "hello notes",
    "data.csv": "a,b,c\n1,2,3",
    "report.md": "# Report\nbody",
    "second.txt": "another text",
    "scratch.tmp": "temporary junk",
}


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t09_"))
    for name, content in _FILES.items():
        (workdir / name).write_text(content, encoding="utf-8")
    state: dict[str, Any] = {"workdir": str(workdir)}
    state["_prompt"] = PROMPT.format(workdir=str(workdir))
    return state


def verify(outcome, state) -> tuple[bool, str]:
    workdir = Path(state["workdir"])
    if not workdir.exists():
        return False, "workdir vanished"

    # .tmp file must be gone (anywhere under workdir).
    stray = list(workdir.rglob("*.tmp"))
    if stray:
        return False, f"stray .tmp not deleted: {[str(p) for p in stray]}"

    # Each real file must now live in <ext>/<name> with content intact.
    expected = {
        "notes.txt": ("txt", "hello notes"),
        "second.txt": ("txt", "another text"),
        "data.csv": ("csv", "a,b,c\n1,2,3"),
        "report.md": ("md", "# Report\nbody"),
    }
    for name, (ext_dir, content) in expected.items():
        target = workdir / ext_dir / name
        if not target.is_file():
            return False, f"{name} not moved to {ext_dir}/ (looked at {target})"
        actual = target.read_text(encoding="utf-8")
        if actual != content:
            return False, f"{name} content changed in {ext_dir}/"
    return True, "files sorted into per-extension folders; .tmp removed; content intact"


def teardown(state) -> None:
    workdir = state.get("workdir")
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)
