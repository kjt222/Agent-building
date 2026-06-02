"""Claude reference run: delete 15 stale orphan latex on the real canvas,
add a properly-grouped (6)(7) derivation panel next to PDF page 4, set
viewport to focus on it. Uses only meta-tier ops (Read/Write/Bash) and
SKILL.md recipe — NO obsidian_* tools."""
import base64, json, re, time, uuid, hashlib
from pathlib import Path
import lzstring

CANVAS = Path(r"D:\D\scientific research vault\文献阅读\SD接触\接触电阻测试"
              r"\A Comparative Evaluation of Different Test Structures for "
              r"the Extraction of Ultralow Specific Contact Resistivity A Review.md")

FENCE_RE = re.compile(r"```compressed-json\s*\n(.*?)\n```", re.DOTALL)
LZ = lzstring.LZString()

# ---- read ----
text = CANVAS.read_text(encoding="utf-8")
m = FENCE_RE.search(text)
body = re.sub(r"\s+", "", m.group(1))
data = json.loads(LZ.decompressFromBase64(body))

# ---- step 1: delete the 15 stale orphan latex elements ----
def is_orphan_latex(e):
    return (
        e.get("type") == "image"
        and not e.get("isDeleted")
        and (e.get("customData") or {}).get("latex_source")
        and not (e.get("groupIds") or [])
        and not e.get("frameId")
    )

old_orphans = [e for e in data["elements"] if is_orphan_latex(e)]
print(f"removing {len(old_orphans)} orphan latex elements")
# Mark isDeleted=True (plugin-safe; preserves history)
for e in old_orphans:
    e["isDeleted"] = True
    e["version"] = (e.get("version") or 1) + 1
    e["updated"] = int(time.time() * 1000)
# Also remove their fileId entries from files{} (orphan dataURLs)
files = data.setdefault("files", {})
for e in old_orphans:
    fid = e.get("fileId")
    if fid and fid in files:
        del files[fid]

# ---- step 2: build the new panel anchored next to page 4 ----
# page 4 frame bbox = (-507, -4466, 734, 950); right margin starts at x=247.
# (6) at canvas y≈-4396, (7) at canvas y≈-4259 (computed via pdfplumber)
panel_group = "g_" + uuid.uuid4().hex[:12]
frame_id    = "frame_" + uuid.uuid4().hex[:12]
now_ms = int(time.time() * 1000)

# 6 latex steps. Panel x=260, widths ~520, heights chosen for each formula.
panel_x = 260
panel_w = 520
panel_y_start = -4470
gap = 14

derivations = [
    # (height, latex)
    (50,  r"R_T = R_{\rm inner} + R_{\rm gap} + R_{\rm outer}"),
    (60,  r"R_{\rm gap} = \int_{r}^{r+L_s}\!\!\frac{R_{\rm sh,s}}{2\pi x}\,dx = "
          r"\frac{R_{\rm sh,s}}{2\pi}\,\ln\!\frac{r+L_s}{r}"),
    (60,  r"V(x)=A\,I_0\!\left(\tfrac{x}{L_t}\right)+B\,K_0\!\left(\tfrac{x}{L_t}\right)"),
    (95,  r"R_T=\frac{R_{\rm sh,s}}{2\pi}\!\left[\ln\!\frac{r+L_s}{r}"
          r"+\frac{L_t}{r}\frac{I_0(r/L_t)}{I_1(r/L_t)}"
          r"+\frac{L_t}{r+L_s}\frac{K_0((r+L_s)/L_t)}{K_1((r+L_s)/L_t)}\right]\;(6)"),
    (45,  r"\text{when }\;r\gg L_t\text{ and }r+L_s\gg L_t:\;"
          r"\frac{I_0}{I_1},\,\frac{K_0}{K_1}\to 1"),
    (80,  r"R_T\;\approx\;\frac{R_{\rm sh,s}}{2\pi}\!\left[\ln\!\frac{r+L_s}{r}"
          r"+\frac{L_t}{r}+\frac{L_t}{r+L_s}\right]\;(7)"),
]

new_elements = []
y = panel_y_start
for h, latex in derivations:
    fid = "lf_" + uuid.uuid4().hex[:12]
    # plugin-managed: empty dataURL, plugin renders via internal katex
    files[fid] = {
        "id": fid,
        "mimeType": "image/svg+xml",
        "dataURL": "",
        "created": now_ms,
    }
    eid = "img_" + uuid.uuid4().hex[:12]
    new_elements.append({
        "type": "image", "id": eid,
        "x": panel_x, "y": y, "width": panel_w, "height": h,
        "angle": 0,
        "strokeColor": "transparent", "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100,
        "groupIds": [panel_group], "frameId": frame_id,
        "roundness": None,
        "seed": now_ms % 2_000_000_000,
        "version": 1, "versionNonce": 0, "isDeleted": False,
        "boundElements": None, "updated": now_ms,
        "link": None, "locked": False,
        "fileId": fid, "scale": [1, 1], "status": "saved",
        "customData": {"latex_source": latex},
    })
    y += h + gap

panel_y_end = y
panel_h = panel_y_end - panel_y_start + 20

# Frame container — visible blue border, named for clarity
frame_el = {
    "type": "frame", "id": frame_id,
    "x": panel_x - 14, "y": panel_y_start - 18,
    "width": panel_w + 28, "height": panel_h + 14,
    "angle": 0,
    "strokeColor": "#1971c2", "backgroundColor": "#e7f5ff",
    "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
    "roughness": 1, "opacity": 50,
    "groupIds": [], "frameId": None, "roundness": None,
    "name": "公式 (6)(7) 推导",
    "seed": now_ms % 2_000_000_000,
    "version": 1, "versionNonce": 0, "isDeleted": False,
    "boundElements": [{"type": "image", "id": e["id"]} for e in new_elements],
    "updated": now_ms,
    "link": None, "locked": False,
}

data["elements"].extend([frame_el] + new_elements)

# ---- step 3: set viewport to focus on the new panel ----
# Excalidraw appState: scrollX/scrollY/zoom for centered ~60% fill
bbox_x, bbox_y = panel_x - 14, panel_y_start - 18
bbox_w, bbox_h = panel_w + 28, panel_h + 14
vp_w, vp_h = 1600, 900
zx = (vp_w * 0.6) / bbox_w
zy = (vp_h * 0.6) / bbox_h
zoom = max(0.1, min(2.0, min(zx, zy)))
cx, cy = bbox_x + bbox_w / 2, bbox_y + bbox_h / 2
sx = vp_w / 2 / zoom - cx
sy = vp_h / 2 / zoom - cy
app = data.setdefault("appState", {})
app["scrollX"] = sx
app["scrollY"] = sy
app["zoom"] = {"value": zoom}
print(f"viewport: scrollX={sx:.1f} scrollY={sy:.1f} zoom={zoom:.3f}")

# ---- step 4: re-encode & write ----
new_body = LZ.compressToBase64(json.dumps(data, separators=(",", ":"),
                                          ensure_ascii=False))
wrapped = "\n\n".join(new_body[i:i+256] for i in range(0, len(new_body), 256))
new_text = FENCE_RE.sub(f"```compressed-json\n{wrapped}\n```", text, count=1)

# Also need to update the `## Embedded Files` section so the plugin can
# katex-render each latex. Plugin keys them by SHA1 of the latex string.
emb_re = re.compile(r"(## Embedded Files\s*\n)(.*?)(?=\n## |\Z)", re.DOTALL)
emb_match = emb_re.search(new_text)
if emb_match:
    existing_section = emb_match.group(2)
    additions = []
    for e in new_elements:
        latex = e["customData"]["latex_source"]
        sha = hashlib.sha1(latex.encode("utf-8")).hexdigest()
        if sha[:8] in existing_section:
            continue
        additions.append(f"{sha}: $${latex}$$\n")
    if additions:
        new_section = existing_section.rstrip() + "\n" + "".join(additions)
        new_text = new_text.replace(emb_match.group(0),
                                    emb_match.group(1) + new_section)
        print(f"appended {len(additions)} sha->latex mappings to Embedded Files")
else:
    print("WARN: no ## Embedded Files section found")

CANVAS.write_text(new_text, encoding="utf-8")
print(f"wrote {CANVAS.stat().st_size} bytes; total elements now "
      f"{len(data['elements'])} (active: "
      f"{sum(1 for e in data['elements'] if not e.get('isDeleted'))})")
