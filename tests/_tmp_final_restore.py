"""Final restore: move SVGs into attachments/, restore .obsidian, rename folder back."""
import shutil, json, os, time
from pathlib import Path

VAULT_NOW = Path(r"D:\D\scientific_research_vault_test")
VAULT_FINAL = Path(r"D:\D\scientific research vault")
HOLD = Path(r"D:\D\_vault_holding")

# 1. Empty the vault (move current content back to hold for clean slate)
print("== Step 1: clear vault root, move all back to hold ==")
items_in_vault = [p for p in VAULT_NOW.iterdir() if p.name not in (".obsidian", ".obsidian_bak_diag")]
for p in items_in_vault:
    dst = HOLD / p.name
    if not dst.exists():
        p.rename(dst)
        print(f"  out: {p.name}")
# Clean .obsidian (the synthesized fresh one)
ob = VAULT_NOW / ".obsidian"
if ob.exists():
    shutil.rmtree(ob)
    print(f"  removed synthesized .obsidian/")

# 2. Restore .obsidian/ from .obsidian_bak_diag (rename — preserves all plugin data)
print("\n== Step 2: restore .obsidian from .obsidian_bak_diag ==")
bak = VAULT_NOW / ".obsidian_bak_diag"
if bak.exists():
    bak.rename(ob)
    print(f"  .obsidian_bak_diag -> .obsidian (full restore)")
else:
    print(f"  WARN: bak not found, .obsidian will be empty")

# 3. Create attachments/ subdir and move SVGs there
print("\n== Step 3: move 60 Pasted Image SVGs into attachments/ ==")
attach = VAULT_NOW / "attachments"
attach.mkdir(exist_ok=True)
moved_svg = 0
for p in sorted(HOLD.iterdir()):
    if p.name.startswith("Pasted Image ") and p.name.endswith(".svg"):
        dst = attach / p.name
        if not dst.exists():
            p.rename(dst)
            moved_svg += 1
print(f"  moved {moved_svg} SVGs into attachments/")

# 4. Move everything else back to vault root
print("\n== Step 4: move remaining items back to vault root ==")
moved_root = 0
for p in sorted(HOLD.iterdir()):
    dst = VAULT_NOW / p.name
    if not dst.exists():
        p.rename(dst)
        moved_root += 1
        print(f"  back: {p.name}")
print(f"  total moved back: {moved_root}")

# 5. Final count
root_items = [p for p in VAULT_NOW.iterdir() if p.name != ".obsidian"]
print(f"\n== Step 5: vault root now has {len(root_items)} items (excl .obsidian) ==")

# 6. Rename vault folder back
print(f"\n== Step 6: rename {VAULT_NOW.name} -> {VAULT_FINAL.name} ==")
if VAULT_FINAL.exists():
    print(f"  ERROR: {VAULT_FINAL} already exists, skipping rename")
else:
    VAULT_NOW.rename(VAULT_FINAL)
    print(f"  renamed.")

# 7. Update obsidian.json
print("\n== Step 7: update obsidian.json ==")
g = Path(os.environ["APPDATA"]) / "obsidian" / "obsidian.json"
data = json.loads(g.read_text(encoding="utf-8"))
# Use the ORIGINAL vault id to preserve any per-vault state
data["vaults"] = {
    "3ba175cae3abd320": {
        "path": str(VAULT_FINAL),
        "ts": int(time.time() * 1000),
        "open": True,
    }
}
g.write_text(json.dumps(data), encoding="utf-8")
print(f"  obsidian.json -> {VAULT_FINAL}")

# 8. Clean up holding folder if empty
print("\n== Step 8: cleanup hold dir ==")
if HOLD.exists():
    leftover = list(HOLD.iterdir())
    if not leftover:
        HOLD.rmdir()
        print(f"  removed empty {HOLD}")
    else:
        print(f"  {len(leftover)} items left in hold (unexpected):")
        for x in leftover:
            print(f"    {x.name}")

print("\nDONE.")
