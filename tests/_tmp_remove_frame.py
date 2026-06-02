"""Remove the (6)(7) frame element. Keep latex elements grouped via groupId so
they still drag together, but no visible border."""
import re, json, time, shutil
from pathlib import Path
import lzstring

MD = Path(r"D:\D\scientific research vault\文献阅读\SD接触\接触电阻测试"
          r"\A Comparative Evaluation of Different Test Structures for the "
          r"Extraction of Ultralow Specific Contact Resistivity A Review.md")

bak = MD.with_suffix(MD.suffix + f".bak_noframe_{int(time.time())}")
shutil.copy2(MD, bak)

text = MD.read_text(encoding="utf-8")
fence_re = re.compile(r"```compressed-json\s*\n(.*?)\n```", re.DOTALL)
m = fence_re.search(text)
body = re.sub(r"\s+", "", m.group(1))
LZ = lzstring.LZString()
data = json.loads(LZ.decompressFromBase64(body))

elements = data["elements"]
frame = next((e for e in elements
              if e.get("type") == "frame" and "6" in (e.get("name") or "")
              and "7" in (e.get("name") or "")), None)
if not frame:
    print("no frame found, nothing to do")
    raise SystemExit(0)

fid = frame["id"]
# Detach children from the frame (clear frameId, keep groupIds for cohesion)
children = [e for e in elements if e.get("frameId") == fid]
for c in children:
    c["frameId"] = None
    c["version"] = (c.get("version") or 1) + 1
    c["updated"] = int(time.time() * 1000)
print(f"detached {len(children)} elements from frame {fid}")

# Mark the frame itself isDeleted (Excalidraw plugin-safe deletion)
frame["isDeleted"] = True
frame["version"] = (frame.get("version") or 1) + 1
frame["updated"] = int(time.time() * 1000)
print(f"marked frame {fid} as isDeleted")

# Recompute & set viewport to focus on group bbox (children only, no frame margin)
xs = [c["x"] for c in children]
ys = [c["y"] for c in children]
ws = [c["width"] for c in children]
hs = [c["height"] for c in children]
bbox_x = min(xs)
bbox_y = min(ys)
bbox_x2 = max(x + w for x, w in zip(xs, ws))
bbox_y2 = max(y + h for y, h in zip(ys, hs))
bbox_w = bbox_x2 - bbox_x
bbox_h = bbox_y2 - bbox_y
vp_w, vp_h = 1600, 900
zoom = max(0.1, min(2.0, min(vp_w * 0.7 / bbox_w, vp_h * 0.7 / bbox_h)))
cx, cy = bbox_x + bbox_w / 2, bbox_y + bbox_h / 2
sx = vp_w / 2 / zoom - cx
sy = vp_h / 2 / zoom - cy
app = data.setdefault("appState", {})
app["scrollX"] = sx
app["scrollY"] = sy
app["zoom"] = {"value": zoom}
print(f"viewport: scrollX={sx:.1f} scrollY={sy:.1f} zoom={zoom:.3f}")

new_body = LZ.compressToBase64(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
wrapped = "\n\n".join(new_body[i:i+256] for i in range(0, len(new_body), 256))
new_text = fence_re.sub(f"```compressed-json\n{wrapped}\n```", text, count=1)
MD.write_text(new_text, encoding="utf-8")
print(f"wrote {MD.stat().st_size} bytes (was {bak.stat().st_size})")
