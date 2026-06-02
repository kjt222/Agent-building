"""Tier A — task 1: read a note's ## Embedded Files section, return sha→latex map.

Pure read-only. Tests whether the model can: (a) open a path-supplied file,
(b) locate a named section, (c) parse a simple per-line format, (d) emit
structured output the verifier can compare.

Verifier extracts the last JSON object from assistant_text and asserts every
ground-truth sha maps to its ground-truth latex.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any


PROMPT = ""  # rewritten in setup() with concrete fixture path
# P18.1.5: all bench tasks use full-access. read-only used to be the right
# default for a "list things" task, but it gives the model a different tool
# manifest than tasks 2/3 — the bench should test under a single uniform
# permission profile so failures aren't tool-availability artifacts.
MODE = "full-access"
TIMEOUT_S = 180.0
# MAX_ITERATIONS intentionally NOT set — default 0 (unlimited) per base.py.


_GROUND_TRUTH: dict[str, str] = {
    "abc123def456": r"E = mc^2",
    "fedcba654321": r"\int_0^\infty e^{-x^2} dx = \frac{\sqrt{\pi}}{2}",
    "deadbeef0000": r"\sum_{n=1}^\infty \frac{1}{n^2} = \frac{\pi^2}{6}",
}


def _build_note() -> str:
    body = "# Test Note\n\nSome surrounding content.\n\n## Embedded Files\n\n"
    for sha, latex in _GROUND_TRUTH.items():
        body += f"{sha}: $${latex}$$\n"
    body += "\n## Other Section\n\nDistractor.\n"
    return body


def setup() -> dict[str, Any]:
    workdir = Path(tempfile.mkdtemp(prefix="harness_bench_t01_"))
    note = workdir / "note_with_embedded.md"
    note.write_text(_build_note(), encoding="utf-8")
    # NOTE on phrasing: "Obsidian note" triggers `file-app-workflow` skill so
    # the manifest exposes Read/Edit/Bash. Without an Obsidian / notebook
    # keyword the prompt falls back to `desktop` scope and Read is not exposed
    # (harness finding recorded in docs/experiments.md, P18 smoke 1).
    prompt = (
        f"Open the Obsidian note at `{note}` and read its `## Embedded Files` "
        f"section. Every line maps a SHA hash to a LaTeX source in the form "
        f"`<sha>: $$<latex>$$`. Return ALL pairs as a single JSON object "
        f"`{{\"<sha>\": \"<latex>\", ...}}` and nothing else after that object."
    )
    return {"workdir": str(workdir), "note": str(note), "_prompt": prompt}


_PAIR_RE = re.compile(r'"([a-f0-9]{8,})"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _extract_pairs(text: str) -> dict | None:
    """Lenient extractor: pull every `"sha": "latex"` pair from the text.

    Strict json.loads fails when models emit LaTeX with bare backslashes
    (`\\int`, `\\frac` — invalid JSON escapes). We grep pairs directly and
    only normalize a few common LaTeX-vs-JSON escape collisions on the value
    side. Returns dict or None if no pairs found.
    """
    pairs = _PAIR_RE.findall(text)
    if not pairs:
        return None
    out: dict[str, str] = {}
    for sha, raw in pairs:
        # Collapse `\\\\foo` → `\foo` (double-escaped) but leave single `\foo`
        # alone so models can emit either form.
        v = raw.replace("\\\\", "\\")
        out[sha] = v
    return out


def verify(outcome, state) -> tuple[bool, str]:
    text = outcome.assistant_text or ""
    parsed = _extract_pairs(text)
    if parsed is None:
        return False, f"no `\"sha\": \"latex\"` pairs found in assistant_text (len={len(text)})"
    missing = [sha for sha in _GROUND_TRUTH if sha not in parsed]
    if missing:
        return False, f"missing sha keys: {missing}"
    wrong = [
        sha for sha, latex in _GROUND_TRUTH.items()
        if str(parsed.get(sha, "")).strip() != latex
    ]
    if wrong:
        sample = {k: (str(parsed.get(k))[:60], _GROUND_TRUTH[k][:60]) for k in wrong[:2]}
        return False, f"latex mismatch on {len(wrong)} key(s); sample {sample}"
    extra = [k for k in parsed if k not in _GROUND_TRUTH]
    if extra:
        return False, f"hallucinated extra keys: {extra[:5]}"
    return True, f"all {len(_GROUND_TRUTH)} sha→latex pairs match"


def teardown(state) -> None:
    wd = state.get("workdir")
    if wd:
        shutil.rmtree(wd, ignore_errors=True)
