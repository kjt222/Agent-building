"""Binary-search .obsidian_bak_diag/ entries to find which one breaks Obsidian.

Usage:
  python tests/_tmp_bisect_obsidian.py copy-jsons      # copy back all top-level JSONs
  python tests/_tmp_bisect_obsidian.py copy-plugins    # copy back plugins/ dir
  python tests/_tmp_bisect_obsidian.py copy-only NAME  # copy back one file/dir by name
  python tests/_tmp_bisect_obsidian.py reset           # delete all in .obsidian/ to fresh state
  python tests/_tmp_bisect_obsidian.py status          # show what's in .obsidian/ now
"""
from pathlib import Path
import shutil, sys, time

VAULT = Path(r"D:\D\scientific research vault")
BAK = VAULT / ".obsidian_bak_diag"
LIVE = VAULT / ".obsidian"

# Files Obsidian re-creates on its own; never our target
SELF_RECREATED = {"app.json", "appearance.json", "core-plugins.json"}


def status():
    print("== LIVE .obsidian/ ==")
    for p in sorted(LIVE.iterdir()):
        print(f"  {p.name}  {'dir' if p.is_dir() else 'file'}  "
              f"size={p.stat().st_size}  mtime={time.ctime(p.stat().st_mtime)}")
    print()
    print("== BAK .obsidian_bak_diag/ ==")
    for p in sorted(BAK.iterdir()):
        print(f"  {p.name}  {'dir' if p.is_dir() else 'file'}  "
              f"size={p.stat().st_size}")


def reset():
    """Clear .obsidian/ to empty so Obsidian recreates baseline next launch."""
    for p in list(LIVE.iterdir()):
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
    print("LIVE .obsidian/ cleared")


def copy_one(name: str):
    src = BAK / name
    dst = LIVE / name
    if not src.exists():
        print(f"NOT IN BAK: {name}")
        return
    if dst.exists():
        if dst.is_dir():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    print(f"copied: {name}")


def copy_jsons():
    """Copy back all top-level JSON files (not plugins/)."""
    for p in sorted(BAK.iterdir()):
        if p.is_file() and p.suffix == ".json":
            copy_one(p.name)


def copy_plugins():
    """Copy back plugins/ subdirectory."""
    copy_one("plugins")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "status":
        status()
    elif cmd == "reset":
        reset()
    elif cmd == "copy-jsons":
        copy_jsons()
    elif cmd == "copy-plugins":
        copy_plugins()
    elif cmd == "copy-only":
        copy_one(sys.argv[2])
    else:
        print("unknown cmd:", cmd)
        sys.exit(1)
