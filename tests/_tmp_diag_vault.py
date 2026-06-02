"""Diagnose why Obsidian throws ENOTDIR on D:\\D\\scientific research vault."""
from pathlib import Path
import os

vault = Path(r"D:\D\scientific research vault")

# 1. Path component check
print("== path component check ==")
for p in [Path("D:/"), Path("D:/D"), vault]:
    print(f"  {p}: exists={p.exists()} is_dir={p.is_dir()} "
          f"is_file={p.is_file()} is_symlink={p.is_symlink()}")

# 2. Top-level entry stat oddities (reparse points, etc)
print()
print("== top-level entry stat check ==")
problems = []
for entry in os.scandir(vault):
    try:
        s = entry.stat(follow_symlinks=False)
        attrs = getattr(s, "st_file_attributes", 0)
        # 0x400 = FILE_ATTRIBUTE_REPARSE_POINT
        # 0x4   = FILE_ATTRIBUTE_SYSTEM
        is_reparse = bool(attrs & 0x400)
        is_system = bool(attrs & 0x4)
        flags = []
        if is_reparse: flags.append("REPARSE")
        if is_system: flags.append("SYSTEM")
        if entry.is_symlink(): flags.append("SYMLINK")
        if flags:
            print(f"  {','.join(flags):20s} {entry.name!r}")
            problems.append(entry.name)
    except Exception as e:
        print(f"  STAT-ERR {entry.name!r}: {e}")
        problems.append(entry.name)
print(f"  problem entries: {len(problems)}")

# 3. Try to imitate what Electron does — scandir each subdirectory recursively
print()
print("== recursive scandir from vault root ==")
err_count = 0
err_paths = []
def walk(d, depth=0):
    global err_count
    try:
        for entry in os.scandir(d):
            if entry.is_dir(follow_symlinks=False):
                walk(entry.path, depth + 1)
    except NotADirectoryError as e:
        err_count += 1
        err_paths.append((str(d), str(e)))
    except Exception as e:
        err_count += 1
        err_paths.append((str(d), f"{type(e).__name__}: {e}"))
walk(vault)
print(f"  scan errors: {err_count}")
for p, e in err_paths[:10]:
    print(f"    {p}  ->  {e}")

# 4. .obsidian config
print()
print("== .obsidian state ==")
cp = vault / ".obsidian" / "community-plugins.json"
if cp.exists():
    print(f"  community plugins: {cp.read_text(encoding='utf-8')}")
plug_dir = vault / ".obsidian" / "plugins"
if plug_dir.exists():
    print(f"  installed plugins dir: {plug_dir}")
    for p in sorted(plug_dir.iterdir()):
        print(f"    {p.name} (dir={p.is_dir()})")

# 5. Workspace.json check
ws = vault / ".obsidian" / "workspace.json"
if ws.exists():
    txt = ws.read_text(encoding="utf-8")
    # Look for any file references that point to non-existent paths
    import re
    files_in_ws = re.findall(r'"file"\s*:\s*"([^"]+)"', txt)
    print()
    print(f"== workspace.json references {len(files_in_ws)} files ==")
    for f in files_in_ws[:20]:
        full = vault / f
        if not full.exists():
            print(f"  MISSING: {f}")
        else:
            print(f"  ok: {f}")
