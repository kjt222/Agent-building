"""Reconstruct a file's session-end content by replaying Write/Edit tool calls
recorded in a Claude Code transcript (.jsonl).

Usage:
    python scripts/reconstruct_from_transcript.py <substring-of-target-path> [out_path]

Replays, in chronological (line) order: the last Write (full content) followed
by every Edit (old_string -> new_string) that targets a path containing the
given substring. Prints the reconstructed content to stdout, or writes it to
out_path if given. Reports any Edit whose old_string was not found (a sign the
replay base diverged — same caveat RECOVERY_NOTES flagged).
"""
from __future__ import annotations

import glob
import json
import sys

TRANSCRIPT_GLOB = r"C:\Users\kjt\.claude\projects\D--D-python---Agent-building\*.jsonl"


def _ops_for(substr: str):
    ops = []
    for f in sorted(glob.glob(TRANSCRIPT_GLOB)):
        for line in open(f, encoding="utf-8"):
            if substr not in line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message") or {}
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                    continue
                inp = b.get("input") or {}
                tgt = str(inp.get("file_path") or inp.get("path") or "")
                if substr not in tgt:
                    continue
                ops.append((b.get("name"), inp))
    return ops


def reconstruct(substr: str):
    ops = _ops_for(substr)
    content = None
    misses = []
    applied = 0
    for name, inp in ops:
        if name == "Write":
            content = inp.get("content", "")
            applied += 1
        elif name == "Edit":
            if content is None:
                continue
            old = inp.get("old_string", "")
            new = inp.get("new_string", "")
            if old and old in content:
                if inp.get("replace_all"):
                    content = content.replace(old, new)
                else:
                    content = content.replace(old, new, 1)
                applied += 1
            else:
                misses.append(old[:80])
    return content, applied, misses, len(ops)


if __name__ == "__main__":
    substr = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    content, applied, misses, total = reconstruct(substr)
    sys.stderr.write(
        f"[{substr}] ops={total} applied={applied} edit_misses={len(misses)}\n"
    )
    for m in misses:
        sys.stderr.write(f"  MISS old_string: {m!r}\n")
    if content is None:
        sys.stderr.write("  no Write found; cannot reconstruct\n")
        sys.exit(1)
    if out:
        open(out, "w", encoding="utf-8").write(content)
        sys.stderr.write(f"  wrote {len(content)} chars -> {out}\n")
    else:
        sys.stdout.write(content)
