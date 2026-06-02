"""Bisect vault top-level entries to find which one triggers Obsidian ENOTDIR.

State machine:
  - HOLDING = D:\\D\\_vault_holding\\
  - VAULT   = D:\\D\\scientific_research_vault_test\\

Commands:
  empty               # move all top-level entries (except .obsidian, .obsidian_bak_diag) to HOLDING
  restore-all         # move everything back
  move-back <names>   # move named items from HOLDING back into VAULT
  move-out  <names>   # move named items from VAULT to HOLDING
  list-vault          # list current vault top-level entries
  list-hold           # list current holding entries
  probe               # check workspace.json appearance after launch (loaded/errored)
  bisect              # automated binary search (requires Obsidian launch between phases)
"""
from pathlib import Path
import shutil, sys, json, time, os

VAULT = Path(r"D:\D\scientific_research_vault_test")
HOLD = Path(r"D:\D\_vault_holding")
PROTECTED = {".obsidian", ".obsidian_bak_diag"}


def list_items(d: Path) -> list[Path]:
    if not d.exists():
        return []
    return sorted([p for p in d.iterdir() if p.name not in PROTECTED])


def move_out(names: list[str]):
    HOLD.mkdir(exist_ok=True)
    for n in names:
        src = VAULT / n
        dst = HOLD / n
        if dst.exists():
            print(f"  SKIP exists in hold: {n}")
            continue
        if not src.exists():
            print(f"  SKIP missing in vault: {n}")
            continue
        src.rename(dst)
        print(f"  out: {n}")


def move_back(names: list[str]):
    for n in names:
        src = HOLD / n
        dst = VAULT / n
        if dst.exists():
            print(f"  SKIP exists in vault: {n}")
            continue
        if not src.exists():
            print(f"  SKIP missing in hold: {n}")
            continue
        src.rename(dst)
        print(f"  back: {n}")


def empty_vault():
    items = list_items(VAULT)
    print(f"moving {len(items)} items out of vault...")
    move_out([p.name for p in items])


def restore_all():
    items = list_items(HOLD)
    print(f"moving {len(items)} items back to vault...")
    move_back([p.name for p in items])


def probe_after_launch(wait_s: float = 5.0) -> str:
    """Verify if vault loaded successfully. Caller should clear .obsidian/ then launch first."""
    ob = VAULT / ".obsidian"
    time.sleep(wait_s)
    if not ob.exists():
        return "no-obsidian-dir"
    has_ws = (ob / "workspace.json").exists()
    return "loaded" if has_ws else "errored"


def cmd_list_vault():
    items = list_items(VAULT)
    print(f"VAULT has {len(items)} non-protected items:")
    for p in items:
        print(f"  {'dir' if p.is_dir() else 'file'}  {p.name!r}")


def cmd_list_hold():
    items = list_items(HOLD)
    print(f"HOLDING has {len(items)} items:")
    for p in items:
        print(f"  {'dir' if p.is_dir() else 'file'}  {p.name!r}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    rest = sys.argv[2:]
    if cmd == "empty":
        empty_vault()
    elif cmd == "restore-all":
        restore_all()
    elif cmd == "move-out":
        move_out(rest)
    elif cmd == "move-back":
        move_back(rest)
    elif cmd == "list-vault":
        cmd_list_vault()
    elif cmd == "list-hold":
        cmd_list_hold()
    elif cmd == "probe":
        print(probe_after_launch())
    else:
        print(f"unknown cmd: {cmd}")
        sys.exit(1)
