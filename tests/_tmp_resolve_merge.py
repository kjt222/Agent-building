"""Resolve the three merge conflicts in docs/{conversation,implementation}.md
per user decisions:

 1. conversation.md conflict #1 (active execution plan): take THEIRS only
    (P14.6 plan retired, P15 plan replaces).
 2. conversation.md conflict #2 (dated history): keep BOTH, theirs first
    (2026-05-20+ P15.4-9), then ours (2026-05-25 P14.6.17 plan).
 3. implementation.md conflict (changelog tail): keep BOTH, ours first
    (continues directly from prior 5/22 entry), then theirs (P15.1-9).
"""
import re
from pathlib import Path

ROOT = Path(r"D:\D\python编程\Agent-building")
HEAD_MARK = "<<<<<<< HEAD"
SEP_MARK = "======="
THEIR_MARK_PREFIX = ">>>>>>>"


def split_conflict(block_text: str) -> tuple[str, str]:
    lines = block_text.split("\n")
    assert lines[0].startswith(HEAD_MARK), lines[0]
    assert lines[-1].startswith(THEIR_MARK_PREFIX), lines[-1]
    sep_idx = next(i for i, ln in enumerate(lines) if ln.strip() == SEP_MARK)
    ours = "\n".join(lines[1:sep_idx])
    theirs = "\n".join(lines[sep_idx + 1 : -1])
    return ours, theirs


def resolve(path: Path, per_conflict_strategy: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"<<<<<<< HEAD\n.*?\n>>>>>>> [^\n]*", re.DOTALL)
    matches = list(pattern.finditer(text))
    assert len(matches) == len(per_conflict_strategy), (
        f"{path.name}: found {len(matches)} conflicts, "
        f"have {len(per_conflict_strategy)} strategies"
    )
    new_text = text
    for m, strategy in zip(reversed(matches), reversed(per_conflict_strategy)):
        ours, theirs = split_conflict(m.group(0))
        if strategy == "theirs":
            replacement = theirs
        elif strategy == "ours":
            replacement = ours
        elif strategy == "both-ours-first":
            sep = "\n" if (ours.endswith("\n") or theirs.startswith("\n")) else "\n\n"
            replacement = ours + sep + theirs
        elif strategy == "both-theirs-first":
            sep = "\n" if (theirs.endswith("\n") or ours.startswith("\n")) else "\n\n"
            replacement = theirs + sep + ours
        else:
            raise ValueError(strategy)
        new_text = new_text[: m.start()] + replacement + new_text[m.end() :]
    path.write_text(new_text, encoding="utf-8")
    remaining = pattern.findall(new_text)
    assert not remaining, f"{path.name}: {len(remaining)} markers remain"
    print(f"{path.name}: resolved {len(matches)} conflict(s), "
          f"{len(text)} -> {len(new_text)} bytes")


resolve(
    ROOT / "docs" / "conversation.md",
    ["theirs", "both-theirs-first"],
)
resolve(
    ROOT / "docs" / "implementation.md",
    ["both-ours-first"],
)
