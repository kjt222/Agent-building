"""File search + `@path` token parsing for the composer @file mention (P12.5).

Two responsibilities, kept together because the back-end search endpoint and
the user-message ``@path`` parser share the same allow/deny rules.

- ``search_files(root, query, limit)``: rank-limited basename match over the
  workspace tree, with cheap pruning of conventional ignored directories.
- ``parse_attached_files(text, root)``: pull ``@path`` tokens out of a user
  message and resolve each to an absolute path *inside the workspace*. Paths
  that escape the root or point at ignored locations are dropped.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".idea",
        ".vscode",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)

DEFAULT_IGNORE_PATH_PREFIXES: tuple[str, ...] = (
    "tests/results/",
    "tests\\results\\",
    "logs/",
    "logs\\",
)

DEFAULT_LIMIT = 20
HARD_LIMIT = 50
MAX_SCAN = 5000

# A relaxed token: leading @ then a path-like body. We tolerate forward and
# back slashes, dots, dashes, underscores, and unicode letters/digits because
# the workspace can contain Chinese filenames. Whitespace, quotes, parens,
# colons, and semicolons terminate the token. Trailing punctuation that is
# almost certainly sentence punctuation (``,.;:!?）)]`) is stripped after the
# fact by ``_strip_trailing_punct``.
_AT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])@([^\s\"'()<>{}\[\]:;]+)")
_TRAILING_PUNCT = ".,;:!?)]}。，；：！？）｝"


@dataclass(frozen=True)
class FileEntry:
    """A single search result row."""

    path: str  # forward-slash relative path from ``root``
    name: str
    kind: str  # "file" or "dir"
    size: int | None = None
    mtime: float | None = None

    def to_dict(self) -> dict:
        d: dict = {"path": self.path, "name": self.name, "kind": self.kind}
        if self.size is not None:
            d["size"] = int(self.size)
        if self.mtime is not None:
            d["mtime"] = float(self.mtime)
        return d


def _is_ignored_dirname(name: str) -> bool:
    if name in DEFAULT_IGNORE_DIRS:
        return True
    return name.startswith(".") and name not in {".", ".."}


def _is_ignored_relpath(rel: str) -> bool:
    norm = rel.replace("\\", "/")
    for prefix in DEFAULT_IGNORE_PATH_PREFIXES:
        if norm.startswith(prefix.replace("\\", "/")):
            return True
    return False


def _score(name: str, query: str) -> int:
    """Rank match quality. Lower = better.

    0  exact basename match (case-insensitive)
    1  prefix match
    2  substring match
    3  no query — fall back to mtime ordering via caller
    """

    if not query:
        return 3
    name_l = name.lower()
    q = query.lower()
    if name_l == q:
        return 0
    if name_l.startswith(q):
        return 1
    if q in name_l:
        return 2
    return 99  # filtered out by caller


def search_files(
    root: Path | str,
    query: str,
    limit: int = DEFAULT_LIMIT,
    *,
    extra_ignore_dirs: Iterable[str] | None = None,
) -> list[FileEntry]:
    """Return up to ``limit`` matching paths under ``root``.

    Empty ``query`` falls back to "most recently modified" ordering, which is
    what the composer wants when the user has just typed ``@`` and nothing
    else. Non-empty queries rank by match kind first, mtime second.
    """

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return []
    limit = max(1, min(int(limit or DEFAULT_LIMIT), HARD_LIMIT))
    ignore_dirs = set(DEFAULT_IGNORE_DIRS)
    if extra_ignore_dirs:
        ignore_dirs.update(extra_ignore_dirs)

    q = (query or "").strip()
    matches: list[tuple[int, float, FileEntry]] = []
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        # Prune ignored directories in-place so os.walk does not descend.
        dirnames[:] = [
            d
            for d in dirnames
            if not _is_ignored_dirname(d) and d not in ignore_dirs
        ]
        rel_dir = os.path.relpath(dirpath, root_path)
        # Filter out the workspace-relative prefixes (tests/results, logs).
        rel_dir_norm = "" if rel_dir == "." else rel_dir.replace("\\", "/")
        if rel_dir_norm and _is_ignored_relpath(rel_dir_norm + "/"):
            dirnames[:] = []
            continue

        for fname in filenames:
            scanned += 1
            if scanned > MAX_SCAN:
                break
            score = _score(fname, q)
            if score >= 99:
                continue
            rel = (
                fname if rel_dir_norm == "" else f"{rel_dir_norm}/{fname}"
            )
            if _is_ignored_relpath(rel):
                continue
            full = Path(dirpath) / fname
            try:
                st = full.stat()
            except OSError:
                continue
            entry = FileEntry(
                path=rel,
                name=fname,
                kind="file",
                size=st.st_size,
                mtime=st.st_mtime,
            )
            # Sort key: score ascending, mtime descending (more recent first).
            matches.append((score, -st.st_mtime, entry))

        if scanned > MAX_SCAN:
            break

    matches.sort(key=lambda t: (t[0], t[1], t[2].path))
    return [m[2] for m in matches[:limit]]


def _strip_trailing_punct(token: str) -> str:
    while token and token[-1] in _TRAILING_PUNCT:
        token = token[:-1]
    return token


def parse_attached_files(
    text: str,
    root: Path | str,
    *,
    extra_ignore_dirs: Iterable[str] | None = None,
) -> list[str]:
    """Extract ``@path`` tokens from ``text`` and resolve to absolute paths.

    A token is included only if:
      - the resolved path exists,
      - it is under ``root`` (no ``..`` escape, no symlink jumps),
      - it does not live under an ignored directory.

    Duplicates are deduped preserving first-seen order.
    Returns absolute filesystem paths as strings.
    """

    if not text:
        return []
    root_path = Path(root).resolve()
    ignore_dirs = set(DEFAULT_IGNORE_DIRS)
    if extra_ignore_dirs:
        ignore_dirs.update(extra_ignore_dirs)

    seen: set[str] = set()
    out: list[str] = []
    for match in _AT_TOKEN_RE.finditer(text):
        raw = _strip_trailing_punct(match.group(1))
        if not raw:
            continue
        candidate = (root_path / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        # Reject paths that escape the workspace.
        try:
            candidate.relative_to(root_path)
        except ValueError:
            continue
        if not candidate.exists():
            continue
        rel_parts = candidate.relative_to(root_path).parts
        if any(p in ignore_dirs or _is_ignored_dirname(p) for p in rel_parts):
            continue
        rel_str = "/".join(rel_parts)
        if _is_ignored_relpath(rel_str):
            continue
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def format_attached_files_block(paths: Sequence[str]) -> str:
    """Render the ``<attached_files>`` system-prompt block.

    Empty input returns the empty string so callers can ``if block:`` cheaply.
    """

    if not paths:
        return ""
    lines = ["<attached_files>"]
    lines.append(
        "Files the user explicitly attached to this turn via @mention. "
        "Treat these paths as load-bearing — they are the targets of the "
        "request unless the user says otherwise. Use Read/WordRead/ExcelRead "
        "etc. to inspect contents; do not guess paths."
    )
    for p in paths:
        lines.append(f"- {p}")
    lines.append("</attached_files>")
    return "\n".join(lines)
