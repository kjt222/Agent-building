"""Deeper vault diagnostic — long paths, illegal Win32 names, .obsidian state."""
from pathlib import Path
import os, json, time

vault = Path(r"D:\D\scientific research vault")
MAX = 260
print("== files with absolute path > 260 chars (Win32 MAX_PATH) ==")
long_paths = []
for p in vault.rglob("*"):
    s = str(p)
    if len(s) > MAX:
        long_paths.append((len(s), s))
long_paths.sort(reverse=True)
for L, s in long_paths[:10]:
    print(f"  len={L}: {s}")
print(f"  total: {len(long_paths)}")

print()
print("== reserved Win32 names anywhere in vault ==")
RESERVED = {"CON","PRN","AUX","NUL"} | {f"COM{i}" for i in range(1,10)} | {f"LPT{i}" for i in range(1,10)}
hits = []
for p in vault.rglob("*"):
    stem = p.name.split(".")[0].upper()
    if stem in RESERVED:
        hits.append(p)
        print(f"  RESERVED: {p}")
print(f"  total: {len(hits)}")

print()
print("== empty .md files at vault root or any 'looks-like-dir but is file' ==")
weird = []
for p in vault.iterdir():
    n = p.name
    if p.is_file() and ("." not in n) and not n.startswith("."):
        weird.append(p)
        print(f"  no-ext file (Obsidian may treat as folder name?): {p}")
print(f"  total: {len(weird)}")

print()
print("== .obsidian directory health ==")
ob = vault / ".obsidian"
for p in sorted(ob.iterdir()):
    s = p.stat()
    print(f"  {p.name}  {'dir' if p.is_dir() else 'file'}  size={s.st_size}  mtime={time.ctime(s.st_mtime)}")
    if p.is_file() and p.suffix == ".json":
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"    JSON-PARSE-ERR: {e}")

print()
print("== check the suspicious vault root special file: 'page_3_content.txt', 'Untitled.canvas' ==")
for n in ["page_3_content.txt", "Untitled.canvas", "software-architecture.excalidrawlib"]:
    p = vault / n
    if p.exists():
        print(f"  {n}  size={p.stat().st_size}  bytes-preview={p.read_bytes()[:80]!r}")
