"""Probe whether Obsidian successfully loaded the vault.

Signal: when vault loads OK, Obsidian writes a fresh workspace.json (mtime
updates) within ~3-5s of launch as it records the current layout. When the
ENOTDIR error page shows, Obsidian never gets that far — only app.json,
appearance.json, core-plugins.json get created (the pre-vault-load init).

Returns: 'loaded', 'errored', or 'unknown'.
"""
from pathlib import Path
import time, sys

VAULT = Path(r"D:\D\scientific research vault")
OB = VAULT / ".obsidian"

def probe(wait_s: float = 5.0) -> str:
    snap_before = {p.name: p.stat().st_mtime for p in OB.iterdir() if p.is_file()}
    has_ws_before = "workspace.json" in snap_before
    ws_mtime_before = snap_before.get("workspace.json")
    print(f"  before: {len(snap_before)} files, workspace.json mtime={ws_mtime_before}")
    time.sleep(wait_s)
    snap_after = {p.name: p.stat().st_mtime for p in OB.iterdir() if p.is_file()}
    has_ws_after = "workspace.json" in snap_after
    ws_mtime_after = snap_after.get("workspace.json")
    print(f"  after({wait_s}s): {len(snap_after)} files, workspace.json mtime={ws_mtime_after}")
    # Diagnostic
    if has_ws_after and not has_ws_before:
        return "loaded"  # Obsidian created it = loaded vault
    if has_ws_after and has_ws_before and ws_mtime_after > ws_mtime_before + 0.5:
        return "loaded"  # Obsidian rewrote it = loaded vault
    if has_ws_before and has_ws_after and abs(ws_mtime_after - ws_mtime_before) < 0.5:
        return "errored"  # untouched → never got that far
    if not has_ws_after:
        return "errored"  # no workspace.json after launch
    return "unknown"

if __name__ == "__main__":
    print(f"probe verdict: {probe()}")
