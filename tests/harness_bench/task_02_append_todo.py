"""Tier A — task 2: append a todo line to ## Tasks without breaking frontmatter.

Tests targeted edit of one section while leaving siblings (frontmatter,
other sections) untouched. Verifier checks the file on disk, not the model's
text — the model can claim anything; only the artifact matters.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .verifiers import strict_check_mtime_advanced


PROMPT = ""
MODE = "full-access"
TIMEOUT_S = 240.0
# MAX_ITERATIONS intentionally NOT set — default 0 (unlimited) per base.py.
# Earlier history: capped at 8, model burned budget on Windows heredoc debug
# before reaching Write; bumped to 12 then removed entirely 2026-05-26 when
# the global no-hard-cap policy landed.


_FIXTURE = (
    "---\n"
    "tags: [project, p18]\n"
    "created: 2026-05-25\n"
    "---\n"
    "\n"
    "# Project Notes\n"
    "\n"
    "## Tasks\n"
    "- [ ] existing task 1\n"
    "- [ ] existing task 2\n"
    "\n"
    "## Notes\n"
    "Other content here. Do not touch.\n"
).encode("utf-8")

_NEW_TODO = "- [ ] write baseline run"

# Byte-level baselines for the two sibling sections that must NOT be touched.
# These are bytes (not str) so any line-ending normalization, trailing-whitespace
# trim, BOM injection, or encoding change is detected.
_FM_BYTES = (
    b"---\n"
    b"tags: [project, p18]\n"
    b"created: 2026-05-25\n"
    b"---\n"
)
_NOTES_BYTES = (
    b"## Notes\n"
    b"Other content here. Do not touch.\n"
)


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t02_"))
    note = workdir / "project_notes.md"
    note.write_bytes(_FIXTURE)
    past_s = note.stat().st_mtime - 5.0
    os.utime(str(note), (past_s, past_s))
    baseline_ns = note.stat().st_mtime_ns
    prompt = (
        f"Append the exact line `{_NEW_TODO}` to the `## Tasks` section "
        f"of the file `{note}`. The frontmatter (the `---` YAML block at "
        f"the top) and the `## Notes` section must remain byte-identical. "
        f"Place the new line after the existing todos in `## Tasks`."
    )
    return {
        "workdir": str(workdir),
        "note": str(note),
        "baseline_mtime_ns": baseline_ns,
        "_prompt": prompt,
    }


def verify(outcome, state) -> tuple[bool, str]:
    note = Path(state["note"])
    ok, reason = strict_check_mtime_advanced(note, int(state["baseline_mtime_ns"]))
    if not ok:
        return False, reason

    # Byte-level read — no decode, no newline translation.
    raw = note.read_bytes()

    # 1. Frontmatter bytes (incl. trailing newline of closing `---`) must be
    #    byte-identical and at offset 0. Catches BOM, CRLF, indent changes,
    #    or any YAML re-emit (key reorder, quote style swap).
    if not raw.startswith(_FM_BYTES):
        # Surface a short prefix diff for debugging.
        got = raw[: len(_FM_BYTES)]
        return False, (
            f"frontmatter bytes drifted (len_got={len(got)}, "
            f"first_diff_offset={_first_diff(_FM_BYTES, got)})"
        )

    # 2. ## Notes section bytes must appear verbatim somewhere after the
    #    frontmatter. Substring on bytes (still strict — no normalization).
    if _NOTES_BYTES not in raw:
        return False, "## Notes section bytes were modified"

    # 3. ## Tasks must still contain both originals AND the new todo, with the
    #    new line AFTER the existing ones. This is the only section the agent
    #    was permitted to mutate.
    text = raw.decode("utf-8", errors="replace")
    tasks_match = re.search(r"## Tasks\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
    if not tasks_match:
        return False, "## Tasks section disappeared"
    tasks_body = tasks_match.group(1)
    for required in ("- [ ] existing task 1", "- [ ] existing task 2", _NEW_TODO):
        if required not in tasks_body:
            return False, f"missing line in ## Tasks: {required!r}"
    if tasks_body.index(_NEW_TODO) < tasks_body.index("- [ ] existing task 2"):
        return False, "new todo was inserted before existing tasks, not appended"

    return True, (
        "frontmatter+## Notes byte-identical; new todo appended to ## Tasks"
    )


def _first_diff(a: bytes, b: bytes) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
