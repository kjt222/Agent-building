---
name: obsidian-excalidraw
description: Edit Obsidian Excalidraw canvases (.excalidraw.md and .excalidraw) — insert text, LaTeX formulas annotated against embedded PDFs, shapes, connectors — while Obsidian is open. Reads PDF pages as PNG for vision-aware placement.
scope: obsidian_excalidraw
priority: 90
triggers:
  - "(?i)\\bexcalidraw\\b|\\.excalidraw\\.md\\b"
  - "obsidian.*画板|excalidraw|公式.*标注|PDF.*注释|PDF.*说明|文献.*笔记.*画"
tools_base:
  - Read
  - Glob
tools:
  - Edit
  - Write
  - Bash
  - FileVerify
---

# Obsidian Excalidraw 操作

> **✅ 优先用专用 `obsidian_*` 工具**（full-access 模式下已注册，2026-06-05 校正）。
> 它们存在、可直接调用，且把最容易出错的环节都烤进了工具层——**优先用它们，不要手搓 Bash 脚本去 lz-string round-trip**：
>
> | 工具 | 干什么 | 关键 |
> |---|---|---|
> | `obsidian_read_excalidraw_canvas` | 读画布，返回结构化元素列表/类型/bbox/element-links | 先读再写，拿真实 element id，别猜 |
> | `obsidian_find_pdf_text_anchor` | 在某 PDF 页里定位一段文字 → 算出画布插入 (x,y) | 放注释/箭头到公式旁边 |
> | `obsidian_add_formula_annotation` | **一步**：给 latex + 说明 + 位置（`anchor_query` 如 `'(1)'` 和/或 `target_xy`），自动渲公式图+排说明文字+画指向箭头+三件套编组 | **加公式注释优先用它**，省得手拼 image/text/arrow |
> | `obsidian_write_excalidraw_elements` | 往 `## Drawing` fence 里 append/replace 元素（更底层） | **LaTeX 只需在 image 元素加 `latex` 字段**，工具自动渲 SVG + 接 fileId/dataURL/宽高，破图不可能 |
> | `obsidian_refresh_note` | open→close→reopen 目标标签 | **写盘后必调**：关掉标签销毁 Obsidian 内存里的旧缓存，重开从磁盘重读，否则你的写入会被开着的标签自动保存冲掉 |
>
> **加公式注释（最省事）**：`read` → `add_formula_annotation`（latex+说明+anchor_query/target_xy，一步出图+文+箭头+组）→ `refresh_note`。
> **需要自定义布局时**：`read` →（定位 `find_pdf_text_anchor`）→ `write_elements`（含 `latex` 字段）→ `refresh_note`。
> 写完**务必 `refresh_note`** 让改动落到用户眼前并战胜"开着的标签把旧内容存回去"的竞争。
>
> **回退**：只有当上述工具确实不可用（报 unknown tool）时，才退回 `Read`/`Write`/`Bash`
> 手动操作 `.excalidraw.md`（lz-string / pdfplumber / matplotlib 计算用 `Bash` 跑
> `.venv/Scripts/python.exe` 脚本；先 `Write` 到 `tests/_tmp_*.py` 再 `Bash` 跑，
> 不要塞进 `python -c "..."`，Windows 路径 + Unicode 易炸）。下文的 Python 配方即为此回退路径保留。

> **🪟 Windows Bash 实操要点**（很多模型在这里浪费整整 3 个 iteration）：
> - 本机 Bash 后端 = Windows shell。`cd "D:\...\中文目录" ; python ...` 在 cmd 里会
>   报"系统找不到指定的路径"（GBK stderr）。**不要 cd**，直接传完整路径：
>   ```
>   D:\D\python编程\Agent-building\.venv\Scripts\python.exe D:\D\python编程\Agent-building\tests\_tmp_my_script.py
>   ```
> - Glob/Read 工具用相对路径就够（它们已经知道 workspace_root）。
> - 脚本里要操作的目标 .md 也用**完整绝对路径**，绝不依赖 `os.getcwd()`。
> - 如果第一次 Bash 失败：换成 `.venv/Scripts/python.exe` 直接跑，不要拼 `cd ... ;`。
> - PowerShell 风格的 `;` 在 cmd 里不工作；要顺序跑两个命令就**分两次 Bash 调用**。
> - 不要试 `npm` / `npx` —— 仓库没装 Node。所有 LaTeX→SVG / lz-string 都是纯 Python。

继承通用流程见 [[file-app-workflow]]：sandbox → FileVerify → backup → Edit → 再 FileVerify。这份只补 Excalidraw 专属知识。

## 第 0 步：定位 vault —— 不要瞎猜

**Obsidian vault 永远不在 agent 的工作目录里**。它是用户文档里的一个独立目录，路径只能从 Obsidian 自己的配置文件读到：

| OS | 配置路径 |
|---|---|
| Windows | `%APPDATA%\obsidian\obsidian.json` |
| macOS | `~/Library/Application Support/obsidian/obsidian.json` |
| Linux | `~/.config/obsidian/obsidian.json` |

```python
# Windows 示例
import os, json
cfg = json.loads(open(os.path.expandvars(r"%APPDATA%\obsidian\obsidian.json"),
                      encoding="utf-8").read())
# vaults 是 {id: {path, ts, open?}}；通常取 open=True 的，或最近 ts 最大的
vaults = [v["path"] for v in cfg["vaults"].values() if os.path.isdir(v["path"])]
```

**绝不允许的反例**：在项目根 `dir` / `ls`，看到 `demo_vault/`、`vault/`、`notes/` 这类名字就直接当成用户 vault。这些大概率是上一次失败任务留下的残骸，agent 在错的文件上"完成"了任务，宿主真 vault 一字未改。**只要不是从 obsidian.json 读出来的路径，都不算 vault**。

## 文件格式速查

**三种**形态都要会识别，前两种是 `.excalidraw.md` 的两种 body 格式，第三种是 `.excalidraw` 原生：

1. **`.excalidraw.md` 的 plain-JSON body**（旧版 / 用户在 plugin 设置里关了 compression）—— `%% ... %%` 之间一段直接可读的 JSON：
   ```
   ---
   excalidraw-plugin: parsed
   ---

   # Excalidraw Data

   ## Text Elements

   ## Embedded Files

   %%
   {"type":"excalidraw","version":2,"source":"...","elements":[...],
    "appState":{...},"files":{...}}
   %%
   ```

2. **`.excalidraw.md` 的 compressed-json body**（Obsidian Excalidraw plugin ≥ 1.9 的**默认值**，你在真实 vault 里多半见到这种）—— body 用 ```` ```compressed-json ```` 围栏包住一段类 base64 字符串（实测插件按 **256 字符换行**，每条内容行后跟一行空行）：
   ```
   ---
   excalidraw-plugin: parsed
   ---

   # Excalidraw Data

   ## Text Elements

   ## Drawing
   ```compressed-json
   N4KAkARALgngDgUwgLgAQQQDwMYEMA2AlgCYBOuA7hADTgQBuCpAzoQPYB2KqATLZMzYBXUtiRoIA...
   ...
   ```
   %%
   ```
   **关键事实** —— 这里不是 zlib / pako，而是 **lz-string**（JS 库 `lz-string` 的 `compressToBase64`，与 pako/deflate 是不同算法）。具体：
   - 字符串首三字符固定是 `N4K`（lz-string base64 字典的前导）
   - 字符串末尾可能是 `===`（**三个**等号）—— 标准 base64 padding 只允许 0/1/2 个，所以**不能直接喂给 `base64.b64decode`**，标准 b64 解码必报 `Invalid base64-encoded string` 或 length 错误
   - 同理 `zlib.decompress(..., -15)` 永远报 `Error -3 invalid block type`（因为根本不是 deflate 数据）
   - 正确解码：`pip install lzstring` 然后 `lzstring.LZString().decompressFromBase64(body_no_whitespace)` 得到 UTF-8 JSON 字符串

   外面 `%%` 之间夹一些 plugin metadata（`{"type":"excalidraw","source":...}` 但 elements 列表为空），**真正的图全在 ```` ```compressed-json ```` 围栏里**。两处的 `version` 都要同步更新，否则 plugin 不刷新。

3. **`.excalidraw`**（原生独立文件）—— 整个文件就是 plain JSON，没有 frontmatter、没有 `%% %%`、没有围栏。

**关键不变**：`elements[]`、`files{}`、`appState{}` 三大顶层 key 必有（在 compressed-json 解开之后）。

### compressed-json 读写完整 recipe（**用 lz-string，不是 pako/zlib**）

**前置依赖**：`pip install lzstring`（仓库 requirements.txt 已加；其他场景如果没装，先 `python -m pip install lzstring`，**不要走 sandbox** —— sandbox 在 Docker overlay 里装，宿主下次跑还是没有）。

```python
import json, re
from pathlib import Path
import lzstring  # pip install lzstring

FENCE_RE = re.compile(r"```compressed-json\s*\n(.*?)\n```", re.DOTALL)
_LZ = lzstring.LZString()

def read_excalidraw_data(path: Path) -> tuple[dict, str]:
    """Returns (data, kind) where kind ∈ {"compressed-json", "plain-json"}."""
    text = path.read_text(encoding="utf-8")
    m = FENCE_RE.search(text)
    if m is not None:
        body = re.sub(r"\s+", "", m.group(1))   # 去掉所有换行/缩进，保留 ===
        decoded = _LZ.decompressFromBase64(body)  # 直接喂；padding 自处理
        if not decoded:
            raise ValueError("lz-string decompression returned empty — "
                             "check fence body was extracted intact")
        return json.loads(decoded), "compressed-json"
    # fallback: plain %% ... %%
    i = text.find("%%"); j = text.find("%%", i + 2)
    if i < 0 or j < 0:
        raise ValueError(f"{path}: no compressed-json fence and no %% block")
    return json.loads(text[i + 2:j].strip()), "plain-json"

def write_excalidraw_data(path: Path, data: dict, kind: str) -> None:
    text = path.read_text(encoding="utf-8")
    if kind == "compressed-json":
        body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        encoded = _LZ.compressToBase64(body)
        # Obsidian 自己写出来是 256 字符宽换行 + 每行后跟一行空行 —— 
        # 保持一致避免 diff 噪音。
        wrapped = "\n\n".join(encoded[i:i+256] for i in range(0, len(encoded), 256))
        new = FENCE_RE.sub(
            f"```compressed-json\n{wrapped}\n```", text, count=1)
    else:
        i = text.find("%%"); j = text.find("%%", i + 2)
        new = text[:i + 2] + "\n" + json.dumps(data, separators=(",", ":"),
                                              ensure_ascii=False) + "\n" + text[j:]
    path.write_text(new, encoding="utf-8")
```

**反例 / 常见错法**（第 5–8 轮 smoke 都中过招）：

- ❌ 用 `base64.b64decode(body)` —— 这块**不是标准 base64**，末尾常出现 `===`（三个等号），标准 b64 解码必报 `Invalid base64-encoded string: number of data characters ... cannot be 1 more than a multiple of 4`。lzstring 的 `decompressFromBase64` 内部按 lz-string 自己的字典处理。
- ❌ 用 `zlib.decompress(raw, -15)` —— 必报 `Error -3 invalid block type`，**这不是 deflate 数据**，是 lz-string 内部算法（与 deflate / pako 完全不同）。
- ❌ 用 `import pako; pako.decompress(...)` —— Python 没有 pako 库；且即便有，pako 是 deflate，跟 lz-string 也不通。
- ❌ 自己手写 base64 + zlib 解码，无论 wbits=15 / -15 / 0 都不通 —— 算法层就错了，参数怎么调都没用。
- ❌ 解码前忘 `re.sub(r"\s+", "", ...)` —— 256 字符换行 + 中间空行的 body 必须先去白。
- ❌ 修改 elements 后忘了同时改 `data["appState"]["lastUpdated"]` / `el["version"] += 1` / `el["updated"] = int(time.time() * 1000)` —— plugin 看不到变化就不重渲。

**识别 / 调试小技巧**：

- 前 3 字符 `N4K`：几乎可以确定是 lz-string compressed-json，不是 plain base64-deflate
- `LZString().decompressFromBase64(body)` 返回**空字符串而不是抛异常**时，通常意味着 body 被截断（fence 切割时把 ``` 或前后行的非内容字符吃进去了）。先 print `body[:30]` 和 `body[-30:]` 确认头尾干净

### compressed-json 改完必跑的 FileVerify

```json
{
  "target": "vault/notes/diagram.excalidraw.md",
  "assertions": [
    {"type": "regex_match", "pattern": "```compressed-json\\s*\\n"},
    {"type": "python_predicate",
     "code": "lambda text, path: __import__('json').loads(__import__('lzstring').LZString().decompressFromBase64(__import__('re').sub(r'\\s+', '', __import__('re').search(r'```compressed-json\\s*\\n(.*?)\\n```', text, __import__('re').DOTALL).group(1)))).get('type') == 'excalidraw'"}
  ]
}
```

第二条断言关键 —— 它做完整 lz-string round-trip，确认结果是 `{"type": "excalidraw", ...}`，等于验证了"我写回去的 compressed-json 块还能被 plugin 同样的解码路径 round-trip 出来"。


## elements 类型（你会用到的）

| type | 关键字段 |
|---|---|
| `text` | `x, y, width, height, text, fontSize, fontFamily` |
| `rectangle / ellipse / diamond` | `x, y, width, height, strokeColor, backgroundColor` |
| `arrow / line` | `x, y, points: [[0,0], [dx,dy], ...], startBinding, endBinding` |
| `image` | `x, y, width, height, fileId, scale` |
| `freedraw` | `points: [...]` |

LaTeX 公式：**是用 image 元素 + customData 实现**：

```json
{
  "type": "image",
  "id": "lf_8b3a...",
  "x": 820, "y": 340,
  "width": 380, "height": 60,
  "fileId": "<svgFileId>",        ← files[<svgFileId>] 是渲染出的 SVG
  "customData": {                 ← 重要：源码留这里，下次可改
    "latex_source": "\\text{当 } v \\to c, \\quad E = \\gamma m c^2"
  }
}
```

`files[<svgFileId>]` 结构：
```json
{
  "id": "<svgFileId>",
  "mimeType": "image/svg+xml",
  "dataURL": "data:image/svg+xml;base64,...",
  "created": <epoch ms>
}
```

## 嵌入的 PDF 怎么找

```python
data = json.loads(json_block)
pdf_pages = [
    (eid, fileId, el) for el in data["elements"]
    if el.get("type") == "image"
    for fileId in [el.get("fileId")]
    if data["files"].get(fileId, {}).get("mimeType") == "application/pdf"
    for eid in [el["id"]]
]
# 每个 PDF page 在 Excalidraw 里是单独一个 image element
# (Obsidian Excalidraw plugin 默认把 PDF 每页拆成一张图)
```

要读取 PDF 内容（agent vision 看图）：
```python
# files[fileId].dataURL 里是 base64 的 PDF 字节 或 PNG
b64 = data["files"][fileId]["dataURL"].split(",", 1)[1]
raw = base64.b64decode(b64)
# 如果 mimeType 是 application/pdf，跑 pymupdf 渲染：
import fitz
doc = fitz.open(stream=raw, filetype="pdf")
page_png = doc[0].get_pixmap(dpi=200).tobytes("png")
# 喂多模态模型读图
```

## 坐标系

- Excalidraw 用**全局坐标**（不是相对 viewport）
- 原点左上，x→右，y→下，单位是 px（不是 EMU 不是 pt）
- `appState.scrollX/scrollY/zoom` 决定**视图**位置，不影响 elements 坐标
- 要"在 PDF 第 N 页右侧插一个元素"：取该 PDF page 的 image element 的 `x + width + margin`，y 用 PDF 内相对坐标 `el.y + (rel_y * el.height)`

## 在 PDF 文本旁定位（pdfplumber 锚点配方）

Excalidraw 画板是**无穷大**的；你给的 (x,y) 哪怕只是 (0,0)，用户首次打开 Obsidian
默认视图（scrollX/Y 在 PDF 中段、zoom ~0.3）就可能完全看不到。"在公式 (6) 旁边插
推导"如果 (x,y) 拍脑袋，用户看到的是空白。

**正确流程**：
1. 找出该 PDF page 在画板里的 image element bbox（`x, y, width, height`）
2. 用 pdfplumber 在 PDF 内定位 "(6)" 等字符串的 bbox（PDF 坐标，**左下原点**）
3. 把 PDF 内归一化坐标映射回画板 element bbox

```python
# tests/_tmp_anchor.py（先 Write 再 Bash 跑）
import json, re, sys
from pathlib import Path
from urllib.parse import unquote
import lzstring, pdfplumber

canvas_path = Path(sys.argv[1])   # e.g. mirror 里的 .excalidraw.md
query       = sys.argv[2]          # e.g. "(6)"
vault_root  = canvas_path.parents[0]
while not (vault_root / ".obsidian").exists() and vault_root != vault_root.parent:
    vault_root = vault_root.parent

text = canvas_path.read_text(encoding="utf-8")
body = re.sub(r"\s+", "",
              re.search(r"```compressed-json\s*\n(.*?)\n```", text, re.DOTALL).group(1))
data = json.loads(lzstring.LZString().decompressFromBase64(body))

# Element Links 段：id → "path.pdf#page=N"
link_sec = re.search(r"## Element Links\n(.*?)(?=\n## |\Z)", text, re.DOTALL)
links = {}
for line in (link_sec.group(1).splitlines() if link_sec else []):
    if ":" in line and not line.lstrip().startswith("#"):
        k, _, v = line.partition(":")
        links[k.strip()] = v.strip()

# 给 query 找候选 page
candidates = []  # (page_no, page_element_id, page_bbox, pdf_path)
for eid, url in links.items():
    m = re.match(r"(.*?\.pdf)#page=(\d+)", unquote(url), re.IGNORECASE)
    if not m: continue
    pdf_rel, page_no = m.group(1), int(m.group(2))
    pdf_path = (vault_root / pdf_rel).resolve()
    if not pdf_path.is_file(): continue
    el = next((e for e in data["elements"] if e.get("id") == eid), None)
    if not el or el.get("type") not in ("image", "frame"): continue
    candidates.append((page_no, eid, (el["x"], el["y"], el["width"], el["height"]), pdf_path))

# 在每个候选 PDF page 上搜 query 字符
results = []
for page_no, eid, (px, py, pw, ph), pdf_path in candidates:
    with pdfplumber.open(pdf_path) as doc:
        if page_no - 1 >= len(doc.pages): continue
        page = doc.pages[page_no - 1]
        page_w, page_h = page.width, page.height
        chars = page.chars
        text_str = "".join(c["text"] for c in chars)
        idx = text_str.find(query)
        if idx < 0: continue
        # 字符 bbox：pdfplumber 左上原点 (page 内)
        c0 = chars[idx]
        last = chars[min(idx + len(query) - 1, len(chars) - 1)]
        x0, y0 = c0["x0"], c0["top"]
        x1, y1 = last["x1"], last["bottom"]
        # 归一化到 page
        nx, ny = x0 / page_w, y0 / page_h
        nw, nh = (x1 - x0) / page_w, (y1 - y0) / page_h
        # 映射回画板
        cx = px + nx * pw
        cy = py + ny * ph
        cw, ch = nw * pw, nh * ph
        # 建议插入点：page 右侧 + y 对齐
        suggested = (px + pw + 20, cy)
        results.append({"page": page_no, "page_element_id": eid,
                        "page_bbox": (px, py, pw, ph),
                        "char_bbox": (cx, cy, cw, ch),
                        "suggested_xy": suggested})

print(json.dumps(results, indent=2, ensure_ascii=False))
```

**fallback**：如果 PDF 是扫描图 (`pdfplumber` 返回空 chars) 或 query 字符串跨行（PDF 文字
按视觉位置不连续）—— 直接落在 `page_bbox` 右侧居中：`(px + pw + 20, py + ph / 2)`。

## Viewport focus —— 写完元素后必须设置 scrollX/scrollY/zoom

新写入的 elements 是**全局坐标**；用户打开 Obsidian 看到的是上次保存的 appState 视图。
如果新内容在画板远处，用户默认视图就是看不到 —— 你的工作完全等于隐形。

**写完元素后必须更新 appState.scrollX/scrollY/zoom** 让首屏聚焦到新内容。计算思路：
让新元素 bbox 居中 + 占视口的 ~60%。

```python
def focus_appstate(data: dict, bbox: tuple[float, float, float, float],
                   viewport_w: int = 1600, viewport_h: int = 900,
                   fill: float = 0.6) -> None:
    """bbox = (x, y, w, h) 是新元素的合并 bbox（不是单个 element）。"""
    x, y, w, h = bbox
    zoom_x = (viewport_w * fill) / max(w, 1.0)
    zoom_y = (viewport_h * fill) / max(h, 1.0)
    zoom_val = max(0.05, min(5.0, min(zoom_x, zoom_y)))
    cx, cy = x + w / 2, y + h / 2
    scroll_x = viewport_w / 2 / zoom_val - cx
    scroll_y = viewport_h / 2 / zoom_val - cy
    app = data.setdefault("appState", {})
    app["scrollX"] = scroll_x
    app["scrollY"] = scroll_y
    # plugin 用的是 {value, associatedElementId?}  *or*  纯数字 —— 兼容写法：
    app["zoom"] = {"value": zoom_val}
```

不设的话，等于"我把公式画了，但放在你画板某个偏远角落，自己找去吧" —— L3 验收必 fail。

## Container strategy（硬规则：frame XOR groupIds，二选一）

⚠️ **同一个 element 不能同时携带非空 `groupIds` 和非空 `frameId`**。Excalidraw frame
不是透明容器，是独立选择单位。两者一起用会出两个 draggable shell：点 frame 边框
只移动 frame，点 group 内容只移动 group，整体无法等比例缩放。

| 想要的效果 | 用 |
|---|---|
| 同选 + 同拖（无可见边框）| `groupIds: [<gid>]`，**不**设 frameId |
| 命名容器 + 可见边框 + 可独立导出 | 加一个 `type=frame` element，子元素只设 `frameId=<frame_id>`，**清空 groupIds** |

写之前过一遍：
```python
for el in new_elements:
    gids = el.get("groupIds") or []
    fid = el.get("frameId")
    if gids and fid:
        raise ValueError(
            f"container-strategy conflict on element {el['id']}: "
            f"has both groupIds={gids} and frameId={fid!r}. Pick one."
        )
```

## 插入新元素的最小代码骨架

```python
import json, uuid, base64, time
from pathlib import Path

path = Path("vault/notes/diagram.excalidraw.md")
text = path.read_text(encoding="utf-8")

# 1. 拆 frontmatter + body + JSON 块
front_end = text.find("---", 3) + 3
front = text[:front_end]
body = text[front_end:]
open_m, close_m = "%%", "%%"
start = body.find(open_m)
end   = body.find(close_m, start + len(open_m))
prefix = body[:start + len(open_m)]
json_str = body[start + len(open_m):end].strip()
suffix = body[end:]
data = json.loads(json_str)

# 2. 新元素
new_id = "n_" + uuid.uuid4().hex[:12]
data["elements"].append({
    "type": "text",
    "id": new_id,
    "x": 820, "y": 340,
    "width": 360, "height": 32,
    "text": "这是 PDF 公式 (1) 的说明",
    "fontSize": 16, "fontFamily": 1,
    "strokeColor": "#1e1e1e",
    "backgroundColor": "transparent",
    "fillStyle": "solid",
    "strokeWidth": 1, "strokeStyle": "solid", "roughness": 1,
    "opacity": 100, "groupIds": [], "frameId": None, "roundness": None,
    "seed": int(time.time() * 1000) % 2_000_000_000,
    "version": 1, "versionNonce": 0, "isDeleted": False,
    "boundElements": None, "updated": int(time.time() * 1000),
    "link": None, "locked": False,
})

# 3. 拼回去 + 写盘（先 .tmp，再 mv）
new_json = json.dumps(data, ensure_ascii=False, indent=None)
new_text = front + prefix + "\n" + new_json + "\n" + suffix
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(new_text, encoding="utf-8")
tmp.replace(path)
```

**重要**：
- 上面 `seed/version/versionNonce/updated/...` 那些字段 Excalidraw 必须存在，缺了 plugin 会报错。Copy 现有 element 改字段比从零写更安全。
- `frameId` / `boundElements` / `link` / `roundness` 可以是 `null`，但必须有 key。

## 插 LaTeX 公式（带可改源码）

### 🚨 主路径（也是唯一推荐）：给 image 元素加 `latex` 字段，`obsidian_write_excalidraw_elements` 自动渲染

**用 `obsidian_write_excalidraw_elements` 工具时**：要渲染一个公式，
**只**在一个 `type="image"` 元素上加一个 `latex` 字段（裸 LaTeX，不带 `$`），
给个 `x`/`y` 坐标，**省略 `fileId` / `width` / `height` / `files{}`**。工具会：

1. 用 matplotlib mathtext 把它渲成 self-contained SVG（剥根 width/height 保 viewBox）；
2. base64 成 `data:image/svg+xml;base64,...` dataURL，塞进 `files{}`；
3. 把元素的 `fileId` 指向它，按公式固有尺寸填 `width`/`height`；
4. 写上 `customData.latex_source` 留源码可再编辑。

```jsonc
// elements 参数里的一个元素，模型只需要写这么多：
{
  "type": "image", "id": "eq1",
  "x": 100, "y": -1400,
  "latex": "\\mathbf{x}_i = \\left(x_i^{H},\\; f_H^{R_1}(x_i^{H}),\\; \\ldots,\\; f_H^{R_M}(x_i^{H})\\right)"
  // 可选: "latex_scale": 1.5, "latex_fontsize": 18, "groupIds": ["g_xxx"]
}
```

工具返回里的 `latex_rendered` 会列出每个被自动渲染的元素。这样**破图不可能发生**——
fileId / dataURL / 宽高 全由框架保证一致，模型不用碰这三件套。

> **⚠️ 不要再走 katex 老路**：以前的做法是只填 `customData.latex_source` + 把
> `SHA1 → $$latex$$` 追加进 `## Embedded Files` 段，靠插件内置 katex 现场渲染。
> 这条路**脆弱**：插件按 `fileId == latex 的 SHA1` 去查，模型几乎总把 fileId 写成
> 随机 uuid、或漏掉 `## Embedded Files` 那条映射，结果画板上是破图占位框
> （2026-06-05 doubao-seed-2-0-pro 真实复盘）。静态 SVG dataURL 不依赖插件 katex 版本，
> 必渲染，所以现在工具直接帮你烤好。

**用户视角注意**：静态 SVG 用 Excalidraw resize handles 拖外框时**公式等比缩放**
（因为根 `<svg>` 只留了 viewBox、没有显式 width/height）。

### 📦 编组：多个相关公式必须 group / frame，否则跟 PPT 拖一个东西全跑差不多

⚠️ 不要把每个 element 的 `groupIds: []` / `frameId: None` 当死规则。
**插入多个相关元素时**（一组公式 / 一个 callout / 一个公式 panel）：

```python
import uuid

panel_group = "g_" + uuid.uuid4().hex[:12]   # 同 group id 整组被选

for latex in formulas:
    # ... build element as above ...
    elem["groupIds"] = [panel_group]          # ← 关键
    data["elements"].append(elem)

# 再加一个 frame 容器：拖 frame 整组跟着移
frame_id = "frame_" + uuid.uuid4().hex[:12]
data["elements"].append({
    "type": "frame", "id": frame_id,
    "x": x0 - 20, "y": y0 - 20, "width": w_total + 40, "height": h_total + 40,
    "name": "公式 (6)(7) 推导",                # frame 显示名
    "angle": 0, "strokeColor": "#1971c2", "backgroundColor": "transparent",
    "fillStyle": "solid", "strokeWidth": 2, "strokeStyle": "solid",
    "roughness": 1, "opacity": 100, "groupIds": [],
    "frameId": None, "roundness": None,
    "seed": int(time.time() * 1000) % 2_000_000_000,
    "version": 1, "versionNonce": 0, "isDeleted": False,
    "boundElements": [], "updated": int(time.time() * 1000),
    "link": None, "locked": False,
})
# 让里面的 element 认 frame 为父
for elem in data["elements"]:
    if elem.get("groupIds") and panel_group in elem["groupIds"]:
        elem["frameId"] = frame_id
```

| 用 | 何时 | 行为 |
|---|---|---|
| `groupIds` | 多元素当一个逻辑组（点一下全选）| 拖任一元素整组一起移；不限制范围 |
| `frame` element + 子元素 `frameId` | 给一组元素显式视觉容器 + 名字 | frame 是真正的"画框"，可命名、可独立导出、移动时整组跟随 |

**反例**（gpt-5.5 round 12 实例）：插了 15 个公式 + 1 个标题文本 + 1 个红框矩形，
全部 `groupIds: []` / `frameId: None` —— 用户拖红框 → 红框跑了，里面 13 个公式不动。
正确做法：13 个 element 共享同一 `groupIds: [<g_uuid>]`，再加 frame 让红框成为容器。

### 🛟 Fallback：matplotlib 渲 SVG（**仅**当目标不是 Obsidian 时）

如果交付目标是裸 `.excalidraw` 文件（excalidraw.com 直接打开，无 plugin），或者
其他不带 katex 的渲染器，那时 `files[fileId].dataURL` 必须真有 SVG base64 ——
这套 matplotlib 链路才有意义。Obsidian vault 任务**不要走这条路**，浪费时间。

#### matplotlib mathtext（项目 .venv 已装）

为什么是它（不是 katex / pylatex / sympy）：
- **纯 Python，离线**：`pip install matplotlib`（仓库 requirements.txt 已加），
  不需要 node / 不需要系统 TeX / 不需要联网
- **直出 SVG self-contained**：savefig 出来的 SVG 内嵌字体路径，无外链 CSS，
  Excalidraw 直接 render，不会缺字
- **mathtext 是 LaTeX 子集**，物理 / 电路 / TLM 类公式（上下标、分数、根号、希腊字母、
  cosh/sinh、积分号）全支持
- **短板**：不支持 `\begin{align}` / `\begin{cases}`。多行公式拆成多个 image element，
  或在一个 `r"$ ... \\ ... $"` 里用 `\\` 换行（mathtext 的有限多行模式）

**反例**：
- ❌ `npx katex --display-mode` —— 第一次跑要 npm cache 下载，无网络就死；
  且 `katex` 出 HTML+CSS 不是单文件 SVG，嵌进 Excalidraw 不渲
- ❌ `sympy.preview(..., output='svg')` —— 调系统 TeX，机器没 TeX 直接 IOError
- ❌ `matplotlib.rcParams['text.usetex'] = True` —— 同样调系统 TeX，**不要开**，
  保持默认 mathtext 才不依赖外部

### Recipe（完整可跑）

```python
import io, base64, json, time, uuid, re
from pathlib import Path
import matplotlib
matplotlib.use("Agg")              # 必须：headless，不开 GUI
import matplotlib.pyplot as plt
import lzstring

# ---- 1. 渲染一段 LaTeX → SVG bytes ----
def render_latex_to_svg(latex_source: str, fontsize: int = 18) -> bytes:
    """latex_source 用裸 LaTeX（不带 $），函数自己包 $...$。
    返回 self-contained，**可在 Excalidraw 容器内自适应缩放**的 SVG 字节串。"""
    fig = plt.figure(figsize=(0.01, 0.01))        # 占位，bbox_inches='tight' 会裁
    fig.text(0.0, 0.0, f"${latex_source}$",
             ha="left", va="bottom", fontsize=fontsize)
    buf = io.BytesIO()
    fig.savefig(buf, format="svg",
                bbox_inches="tight",              # 关键：裁到内容尺寸
                pad_inches=0.05,                  # 留一点白边，免得 stroke 被剪
                transparent=True)                  # 透明背景，Excalidraw 主题色不冲突
    plt.close(fig)
    svg = buf.getvalue().decode("utf-8")
    # ⚠️ 关键：剥掉根 <svg> 的 width="...pt" / height="...pt"，**保留 viewBox**。
    # matplotlib 默认输出 <svg width="166pt" height="51pt" viewBox="0 0 166 51" ...>。
    # 带显式尺寸时 Excalidraw 用户拖外框时**只缩放外框、不缩放公式**（SVG 自称我就这么大）。
    # 剥掉 width/height 只留 viewBox 后，SVG 变成"自适应容器"，外框拉大公式等比例放大。
    import re as _re
    svg = _re.sub(r'(<svg[^>]*?)\s+width="[^"]+"', r"\1", svg, count=1)
    svg = _re.sub(r'(<svg[^>]*?)\s+height="[^"]+"', r"\1", svg, count=1)
    return svg.encode("utf-8")

# ---- 2. SVG 估算像素 width/height（Excalidraw 要 px 数）----
def svg_pixel_size(svg_bytes: bytes) -> tuple[float, float]:
    """从 viewBox 读出固有尺寸（pt），1pt = 1.333 px。
    注意 render_latex_to_svg 已剥掉根 <svg> 的 width/height —— 只能从 viewBox 取。"""
    head = svg_bytes[:512].decode("ascii", errors="ignore")
    m = re.search(r'viewBox="\s*[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"', head)
    if m is None:
        raise ValueError(f"SVG missing viewBox: head={head[:200]!r}")
    w_pt, h_pt = float(m.group(1)), float(m.group(2))
    return w_pt * 1.333, h_pt * 1.333

# ---- 3. 插入一个新 LaTeX image element ----
def insert_latex_image(data: dict, latex_source: str, x: float, y: float,
                       scale: float = 1.5) -> str:
    """改 data in-place，返回新 element 的 id。"""
    svg_bytes = render_latex_to_svg(latex_source)
    w_px, h_px = svg_pixel_size(svg_bytes)
    w, h = w_px * scale, h_px * scale

    fid = "lf_" + uuid.uuid4().hex[:12]
    now_ms = int(time.time() * 1000)
    data.setdefault("files", {})[fid] = {
        "id": fid,
        "mimeType": "image/svg+xml",
        "dataURL": "data:image/svg+xml;base64,"
                   + base64.b64encode(svg_bytes).decode("ascii"),
        "created": now_ms,
        "lastRetrieved": now_ms,
    }

    eid = "img_" + uuid.uuid4().hex[:12]
    data["elements"].append({
        "type": "image", "id": eid,
        "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": "transparent",
        "backgroundColor": "transparent",
        "fillStyle": "solid", "strokeWidth": 1, "strokeStyle": "solid",
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None,
        "seed": now_ms % 2_000_000_000,
        "version": 1, "versionNonce": 0, "isDeleted": False,
        "boundElements": None, "updated": now_ms,
        "link": None, "locked": False,
        "fileId": fid, "scale": [1, 1], "status": "saved",
        "customData": {"latex_source": latex_source},    # 保留可再编辑的源码
    })
    return eid
```

### 调用（在 compressed-json round-trip 里）

```python
path = Path(target_md)
data, kind = read_excalidraw_data(path)               # 见上面 lz-string recipe

insert_latex_image(data,
    r"\rho_c = R_{sh,s}\,L_t^{2},\quad L_t = \sqrt{\rho_c / R_{sh,s}}",
    x=900, y=400, scale=1.5)

insert_latex_image(data,
    r"V(x) = I\,R_{sh,s}\,\frac{\cosh\left((L-x)/L_t\right)}{\sinh(L/L_t)}",
    x=900, y=520, scale=1.5)

write_excalidraw_data(path, data, kind)
```

### 反例（不要踩）

- ❌ `fig.text(..., r"\$E=mc^2\$")` 双引号里写 `$` 还转义 —— 直接 `r"$E=mc^2$"` 就好。
- ❌ 忘 `bbox_inches="tight"` —— SVG 是默认 figsize 的整张白板，公式只在中间一小点。
- ❌ 忘 `matplotlib.use("Agg")` —— Windows 上 import pyplot 默认要 Tk，没 Tk 就 ImportError。
- ❌ 忘 `transparent=True` —— SVG 自带白底，盖在 Excalidraw 深色主题上瞎眼。
- ❌ `plt.close(fig)` 漏掉 —— 跑几十个公式后 figure 句柄爆掉 RuntimeWarning。
- ❌ 用 `data:image/svg+xml,<svg ...>` 直接塞原始 SVG（不 base64） —— Excalidraw plugin
  解 `dataURL` 用的是 `atob(...)`，必须 base64 编码。
- ❌ width/height 直接用 SVG 的 pt 值不乘 1.333 —— Excalidraw 单位是 CSS px，1pt = 1.333px，
  不换算公式会缩成原来 75%。
- ❌ 用 `\bigl(...\bigr)` / `\Big(...\Big)` —— mathtext **不支持** big 系列分隔符，
  直接 `ParseFatalException: Unknown symbol: \bigl`。用 `\left(...\right)` 替代
  （自动按内容高度撑大括号）。
- ❌ 用 `\begin{align}` / `\begin{cases}` / `\substack` —— mathtext 不支持环境。多行公式
  拆成多个 image element（推荐），或在一个 `r"$ A \\ B $"` 里用裸 `\\` 强制换行。

### 改已有 LaTeX 公式（只换源码 + 重渲）

```python
target = next(e for e in data["elements"]
              if e.get("type") == "image"
              and (e.get("customData") or {}).get("latex_source")
              and "old keyword" in e["customData"]["latex_source"])

new_source = r"\rho_c = R_{sh,s}\,L_t^{2}"
svg_bytes = render_latex_to_svg(new_source)
data["files"][target["fileId"]]["dataURL"] = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(svg_bytes).decode("ascii"))
target["customData"]["latex_source"] = new_source
target["version"] = (target.get("version") or 1) + 1
target["updated"] = int(time.time() * 1000)
# x/y 不动；如果新公式宽度变了，可以再算 w/h 更新
```

## 改已有 LaTeX 公式

```python
# 找到那个 image element by customData.latex_source 关键词
target = next(e for e in data["elements"]
              if e.get("type") == "image"
              and (e.get("customData") or {}).get("latex_source")
              and "old keyword" in e["customData"]["latex_source"])

# 重渲新 LaTeX → SVG
new_svg = render_latex_to_svg(new_source)  # 同上 Bash sandbox
data["files"][target["fileId"]]["dataURL"] = f"data:image/svg+xml;base64,..."
target["customData"]["latex_source"] = new_source
target["version"] = (target.get("version") or 1) + 1
target["updated"] = int(time.time() * 1000)
```

## 改完必跑的 FileVerify

```json
{
  "target": "vault/notes/diagram.excalidraw.md",
  "assertions": [
    {"type": "regex_match", "pattern": "excalidraw-plugin:\\s*parsed"},
    {"type": "extracted_block_parses", "between": ["%%", "%%"], "as": "json"},
    {"type": "json_path_equals", "between": ["%%", "%%"],
     "path": "type", "value": "excalidraw"},
    {"type": "json_path_count_min", "between": ["%%", "%%"],
     "path": "elements.*", "min": <旧元素数 + 新增数>},
    {"type": "json_path_exists", "between": ["%%", "%%"],
     "path": "elements.*.customData.latex_source"},
    {"type": "python_predicate",
     "between": ["%%", "%%"],
     "code": "lambda d: all('id' in e and 'seed' in e for e in d['elements'])"}
  ]
}
```

最后一条 `python_predicate` 是关键 —— Excalidraw plugin 要求每个 element 都有 `id` 和 `seed`，缺了直接报错"can't load canvas"。

### 公式插入后必跑的 FileVerify（**专查 dataURL 没漏填**）

这是用户视角看不看得到公式的唯一硬指标 —— 有 `latex_source` 但 `files[fileId].dataURL`
为空 / 缺 / 不是 svg base64 → Obsidian 显示占位框。

```json
{
  "target": "vault/notes/diagram.excalidraw.md",
  "assertions": [
    {"type": "python_predicate",
     "code": "lambda text, path: __import__('json').loads(__import__('lzstring').LZString().decompressFromBase64(__import__('re').sub(r'\\s+', '', __import__('re').search(r'```compressed-json\\s*\\n(.*?)\\n```', text, __import__('re').DOTALL).group(1))))"},
    {"type": "python_predicate",
     "code": "def _check(text, path):\n    import json, re, lzstring\n    body = re.sub(r'\\s+', '', re.search(r'```compressed-json\\s*\\n(.*?)\\n```', text, re.DOTALL).group(1))\n    data = json.loads(lzstring.LZString().decompressFromBase64(body))\n    latex_imgs = [e for e in data['elements'] if e.get('type')=='image' and (e.get('customData') or {}).get('latex_source')]\n    assert latex_imgs, 'no latex image element found'\n    for e in latex_imgs:\n        fid = e.get('fileId'); assert fid, f'image {e[\"id\"]} missing fileId'\n        f = (data.get('files') or {}).get(fid)\n        assert f, f'fileId {fid} not in files[]'\n        url = f.get('dataURL') or ''\n        assert url.startswith('data:image/svg+xml;base64,'), f'fileId {fid} dataURL not svg base64: {url[:40]!r}'\n        assert len(url) > 500, f'fileId {fid} dataURL too short ({len(url)}B) — likely empty SVG'\n    return True\n_check"}
  ]
}
```

第二条断言会枚举每个带 `latex_source` 的 image，强制 `files[fileId].dataURL` 不仅存在、
而且是 `data:image/svg+xml;base64,...` 开头、长度 > 500B（小于这个长度的 SVG 大概率是没渲出来 / 只有空 `<svg/>` 头）。

## Obsidian reload 实测

- Excalidraw plugin 监听 vault 文件 mtime
- 外部写盘 100-500ms 后画板自动刷新
- **没**「外部修改提示对话框」 —— 跟 Jupyter 不一样，所以并发写要小心
- 用户正在拖元素时你写文件：用户的下次自动保存（停止操作后 ~1s）会盖掉你的写入。先 `stat` mtime，<2s 内有用户活动就稍等

## 常见坑

- **JSON 缩进**：`json.dumps(data)` 默认 separators 是 `(', ', ': ')`，Excalidraw 自己写出的是 `(",", ":")`（紧凑）。两种 plugin 都能读，但 diff 起来嘈杂，建议跟原文件风格保持一致
- **中文 / 公式特殊字符**：`json.dumps(..., ensure_ascii=False)` 别忘
- **文件结尾换行**：原文件结尾一般有 `\n`，写回去也保留
- **plugin "Force re-save"**：如果你改完 plugin 没刷新，让用户 Ctrl+P → "Excalidraw: force re-save" 兜底
- **fileId 长度**：plugin 生成的是 40 字符 hex，但其实任意长字符串都能用 —— 我们用 `n_` + 12 hex 够了
