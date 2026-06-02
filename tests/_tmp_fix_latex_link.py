"""Fix the (6)(7) panel: rewire element.fileId -> sha1(latex), add Embedded Files."""
import re, json, hashlib, time, shutil
from pathlib import Path
import lzstring

MD = Path(r"D:\D\scientific research vault\文献阅读\SD接触\接触电阻测试"
          r"\A Comparative Evaluation of Different Test Structures for the "
          r"Extraction of Ultralow Specific Contact Resistivity A Review.md")

# Backup
bak = MD.with_suffix(MD.suffix + f".bak_fixlink_{int(time.time())}")
shutil.copy2(MD, bak)
print(f"backup: {bak.name}  size={bak.stat().st_size}")

text = MD.read_text(encoding="utf-8")
fence_re = re.compile(r"```compressed-json\s*\n(.*?)\n```", re.DOTALL)
m = fence_re.search(text)
body = re.sub(r"\s+", "", m.group(1))
LZ = lzstring.LZString()
data = json.loads(LZ.decompressFromBase64(body))

elements = data["elements"]
files = data.setdefault("files", {})

# Find the (6)(7) frame and its child latex images
frame = next((e for e in elements
              if e.get("type") == "frame" and "6" in (e.get("name") or "")
              and "7" in (e.get("name") or "")), None)
print(f"frame: {frame.get('id') if frame else None}  name={frame.get('name') if frame else None}")
children = [e for e in elements if e.get("frameId") == frame["id"] and not e.get("isDeleted")]
print(f"frame children: {len(children)}")

# Rewire each child: fileId := sha1(latex_source), update files{}
sha_to_latex = {}
for el in children:
    latex = (el.get("customData") or {}).get("latex_source")
    if not latex:
        print(f"  WARN no latex_source on {el.get('id')}, skipping")
        continue
    old_fid = el.get("fileId")
    sha = hashlib.sha1(latex.encode("utf-8")).hexdigest()
    sha_to_latex[sha] = latex
    # Move file entry to new key (sha)
    if old_fid in files and old_fid != sha:
        files[sha] = {**files.pop(old_fid), "id": sha}
    elif sha not in files:
        files[sha] = {"id": sha, "mimeType": "image/svg+xml", "dataURL": "",
                      "created": int(time.time() * 1000)}
    el["fileId"] = sha
    el["version"] = (el.get("version") or 1) + 1
    el["updated"] = int(time.time() * 1000)
    print(f"  {el['id']}: fileId {old_fid!r} -> {sha[:12]}...  ({len(latex)} char latex)")

# Re-encode and write back into fence
new_body = LZ.compressToBase64(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
wrapped = "\n\n".join(new_body[i:i+256] for i in range(0, len(new_body), 256))
new_text = fence_re.sub(f"```compressed-json\n{wrapped}\n```", text, count=1)

# Now ensure each sha is in `## Embedded Files` section as `<sha>: $$<latex>$$`
emb_re = re.compile(r"(## Embedded Files\s*\n)(.*?)(?=\n## |\Z)", re.DOTALL)
emb_m = emb_re.search(new_text)
if not emb_m:
    print("WARN: no '## Embedded Files' section, creating before %% closer")
    # Insert before final %%
    new_text = new_text.replace("\n%%", "\n\n## Embedded Files\n\n%%", 1)
    emb_m = emb_re.search(new_text)

existing = emb_m.group(2)
added = 0
for sha, latex in sha_to_latex.items():
    if sha in existing:
        # already mapped (likely to a PDF page); update to point to latex
        # Replace the line for this sha
        existing = re.sub(rf"^{sha}:.*$", f"{sha}: $${latex}$$", existing, count=1, flags=re.MULTILINE)
    else:
        existing = existing.rstrip() + f"\n{sha}: $${latex}$$\n"
        added += 1

new_text = new_text[:emb_m.start(2)] + existing + new_text[emb_m.end(2):]
print(f"Embedded Files: added {added} new sha->latex mappings, updated existing if any")

MD.write_text(new_text, encoding="utf-8")
print(f"wrote {MD.stat().st_size} bytes (was {bak.stat().st_size})")
