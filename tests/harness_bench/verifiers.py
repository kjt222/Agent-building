"""Shared strict verifiers used by multiple harness_bench tasks.

Every check here returns ``(ok: bool, reason: str)``. Reason is non-empty in
both branches so failed runs always have a human-readable diagnosis.

Keep checks *strict* — false positives here mean the whole bench lies. Tier D
sanity tasks (task_13, task_14) verify these red-lights actually fire.
"""

from __future__ import annotations

from pathlib import Path


def strict_check_fileids(elements: list[dict], files: dict[str, dict]) -> tuple[bool, str]:
    """Every non-deleted element.fileId must resolve to a non-empty, plausibly-
    sized dataURL. Used by Tier B tasks 5 (fix orphan), 6 (rebind latex) and
    Tier D task 13 (sanity)."""
    for el in elements:
        if el.get("isDeleted"):
            continue
        fid = el.get("fileId")
        if not fid:
            continue
        if fid not in files:
            return False, f"element {el.get('id')} references missing fileId {fid!r}"
        data_url = files[fid].get("dataURL", "")
        if not data_url:
            return False, f"fileId {fid!r} dataURL is empty"
        if data_url.startswith("data:") and len(data_url) < 32:
            return False, f"fileId {fid!r} dataURL too short ({len(data_url)} chars)"
    return True, "all fileIds valid"


def strict_check_mtime_advanced(
    path: Path,
    baseline_mtime_ns: int,
    *,
    min_delta_ns: int = 1_000_000,
) -> tuple[bool, str]:
    """File must have been touched after baseline. ns-resolution to dodge FAT
    2-second granularity. Used by Tier A task 2 (append todo), Tier D task 14
    (sanity)."""
    if not path.exists():
        return False, f"target file disappeared: {path}"
    cur = path.stat().st_mtime_ns
    if cur <= baseline_mtime_ns:
        return False, f"mtime did not advance: baseline={baseline_mtime_ns} now={cur}"
    delta = cur - baseline_mtime_ns
    if delta < min_delta_ns:
        return False, (
            f"mtime advance suspiciously small: {delta}ns "
            f"(threshold={min_delta_ns}ns)"
        )
    return True, f"mtime advanced by {delta}ns"
