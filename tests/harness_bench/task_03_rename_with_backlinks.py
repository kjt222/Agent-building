"""P18-A — task 3: rename a note and update all `[[old]]` backlinks.

Vault has 4 notes; 3 backlinks point to `old_note` (1 plain, 1 with display
alias, 1 in a bullet). After the agent runs, `old_note.md` must be gone,
`new_note.md` must exist with original content, and zero `[[old_note]]` /
`[[old_note|` references may remain — all 3 must be re-pointed to `new_note`.

INTENT — external vault, full-access mode.

The fixture lives under %TEMP% (outside the agent's workspace_root) because
real Obsidian vaults usually live outside the project tree. With MODE set
to `full-access` (P18.1.5), Edit/Write are NOT path-blocked here — the
model can take either path: direct Edit on each note, or `mv` + scripted
regex via Bash. This task therefore tests **strategy choice + completion**
under realistic conditions, not a forced Bash-fallback workaround.

History: before P18.1.5 the workspace boundary blocked Edit on external
paths even in full-access mode, which forced models into clumsy Bash-only
solutions and exposed the silent-script-abandonment pattern. That extra
constraint is gone; if a model still chooses to write a helper script and
not run it, that's a model behavior issue, not an environment artifact.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import Any


PROMPT = ""
MODE = "full-access"
TIMEOUT_S = 300.0
# MAX_ITERATIONS intentionally NOT set — default 0 (unlimited) per base.py.


_OLD_NAME = "old_note"
_NEW_NAME = "new_note"

_FILES = {
    "old_note.md": "# Old Note\n\nSome original content that should survive the rename.\n",
    "note_a.md":   "# A\n\nSee [[old_note]] for context. Also [[old_note|the alias here]] inline.\n",
    "note_b.md":   "# B\n\n- [[old_note]] reference in bullet\n- unrelated bullet\n",
    "note_c.md":   "# C\n\nThis note has no backlinks. Leave it alone.\n",
}


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t03_"))
    vault = workdir / "vault"
    vault.mkdir()
    for name, content in _FILES.items():
        (vault / name).write_text(content, encoding="utf-8")

    prompt = (
        f"In the Obsidian vault at `{vault}`, rename `{_OLD_NAME}.md` to "
        f"`{_NEW_NAME}.md` and update EVERY backlink across all other notes "
        f"so `[[{_OLD_NAME}]]` becomes `[[{_NEW_NAME}]]` and "
        f"`[[{_OLD_NAME}|alias]]` becomes `[[{_NEW_NAME}|alias]]`. "
        f"The file's body content must be preserved exactly. Notes without "
        f"backlinks must not be modified."
    )
    note_c_baseline = (vault / "note_c.md").read_text(encoding="utf-8")
    return {
        "workdir": str(workdir),
        "vault": str(vault),
        "note_c_baseline": note_c_baseline,
        "_prompt": prompt,
    }


def verify(outcome, state) -> tuple[bool, str]:
    vault = Path(state["vault"])
    old = vault / f"{_OLD_NAME}.md"
    new = vault / f"{_NEW_NAME}.md"

    if old.exists():
        return False, f"old file still present: {old.name}"
    if not new.exists():
        return False, f"new file missing: {new.name}"

    new_text = new.read_text(encoding="utf-8")
    if "Some original content that should survive the rename." not in new_text:
        return False, "renamed file lost its original body content"

    # Count remaining old refs across the vault (not counting old.md which is gone).
    old_plain = re.compile(rf"\[\[{re.escape(_OLD_NAME)}\]\]")
    old_alias = re.compile(rf"\[\[{re.escape(_OLD_NAME)}\|")
    new_plain = re.compile(rf"\[\[{re.escape(_NEW_NAME)}\]\]")
    new_alias = re.compile(rf"\[\[{re.escape(_NEW_NAME)}\|")

    old_hits = new_hits = 0
    for md in vault.glob("*.md"):
        txt = md.read_text(encoding="utf-8")
        old_hits += len(old_plain.findall(txt)) + len(old_alias.findall(txt))
        new_hits += len(new_plain.findall(txt)) + len(new_alias.findall(txt))

    if old_hits != 0:
        return False, f"{old_hits} `[[{_OLD_NAME}]]`-style backlink(s) still present"
    if new_hits != 3:
        return False, (
            f"expected 3 `[[{_NEW_NAME}]]` backlinks after rename, "
            f"found {new_hits}"
        )

    note_c_now = (vault / "note_c.md").read_text(encoding="utf-8")
    if note_c_now != state["note_c_baseline"]:
        return False, "note_c.md (which had no backlinks) was modified"

    return True, "renamed file, 3 backlinks rewritten, note_c untouched"


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
