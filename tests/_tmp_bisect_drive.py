"""Drive the bisect: move named items from hold back to vault, then probe.

Caller passes a list of indices into the sorted hold listing (so we can
specify CJK names cleanly without shell-quoting issues).
"""
import shutil, sys, time, json, os
from pathlib import Path

VAULT = Path(r"D:\D\scientific_research_vault_test")
HOLD = Path(r"D:\D\_vault_holding")
PROTECTED = {".obsidian", ".obsidian_bak_diag"}

def hold_items_sorted():
    return sorted([p for p in HOLD.iterdir() if p.name not in PROTECTED], key=lambda p: p.name)

def vault_items_sorted():
    return sorted([p for p in VAULT.iterdir() if p.name not in PROTECTED], key=lambda p: p.name)

def reset_state(items_back_to_hold: bool = True):
    """Move everything back to hold (clean baseline)."""
    if items_back_to_hold:
        for p in vault_items_sorted():
            (HOLD / p.name).exists() or p.rename(HOLD / p.name)

def move_indices_in(indices: list[int]):
    items = hold_items_sorted()
    moved = []
    for i in indices:
        if 0 <= i < len(items):
            src = items[i]
            dst = VAULT / src.name
            if not dst.exists():
                src.rename(dst)
                moved.append(src.name)
    return moved

def clear_obsidian():
    ob = VAULT / ".obsidian"
    if ob.exists():
        shutil.rmtree(ob)

def probe_after(launch_wait_s: float = 6.0) -> str:
    time.sleep(launch_wait_s)
    ob = VAULT / ".obsidian"
    if not ob.exists():
        return "no-obsidian-dir"
    return "loaded" if (ob / "workspace.json").exists() else "errored"

def main():
    cmd = sys.argv[1]
    if cmd == "list":
        for i, p in enumerate(hold_items_sorted()):
            print(f"  [{i:2d}] {'dir' if p.is_dir() else 'file'}  {p.name!r}")
    elif cmd == "reset-to-hold":
        for p in vault_items_sorted():
            dst = HOLD / p.name
            if not dst.exists():
                p.rename(dst)
        print("vault cleaned, all items back to hold")
    elif cmd == "setup":
        # clear obsidian + move specified indices in
        indices = [int(x) for x in sys.argv[2:]]
        clear_obsidian()
        # Also empty vault first
        for p in vault_items_sorted():
            dst = HOLD / p.name
            if not dst.exists():
                p.rename(dst)
        moved = move_indices_in(indices)
        print(f"setup: cleared .obsidian, moved {len(moved)} items in:")
        for m in moved:
            print(f"  {m!r}")
    elif cmd == "probe":
        print(probe_after())
    else:
        print(f"unknown: {cmd}")

if __name__ == "__main__":
    main()
