---
name: file-app-workflow
description: General pattern for editing files that are open in another desktop app (Obsidian, Jupyter, drawio, Notion exports, …). Sandbox-first, assert, then real-edit with backup.
scope: file_app
priority: 80
triggers:
  - "(?i)\\bobsidian\\b|\\bexcalidraw\\b|\\bjupyter\\b|\\bnotebook\\b|\\bdrawio\\b|\\bnotion\\b|\\bsketch\\b|\\bfigma\\b"
  - "笔记|画板|思维导图|脑图|笔记本"
tools_base:
  - Read
  - Glob
  - Grep
tools:
  - Edit
  - Write
  - Bash
  - FileVerify
---

# 通用流程 — 编辑「另一个程序打开着的文件」

适用于：Obsidian、Jupyter Lab/Notebook、drawio-desktop、Notion 导出、各种 markdown / JSON / XML 后端的桌面应用。

**不适用**：纯二进制 + 有状态 IPC 的应用（Word/Excel/PowerPoint via COM、Photoshop 等），那些走 `office_*` 或 `presentation-edit` 等专门 skill。

## 核心 5 步

```
1. READ        Read 目标文件，先识别格式（YAML/JSON/markdown/XML/混合）
2. SANDBOX     用 Bash(sandbox=true, command="python -c '...'") 在 Docker
               隔离副本里写并跑修改脚本 —— 不动用户原文件
3. VERIFY      FileVerify 检查改后文件的结构断言（schema/key/regex/count）
4. APPLY       通过后再 Edit 用户的真文件；改前先 cp 到 .bak/ 做 backup
5. WAIT/CONFIRM 等 1-2s 让 app 监听 mtime 后自动 reload；可再 FileVerify 一次最终态
```

> **Helper scripts are deliverables only after they run.** If you `Write` a
> `.py / .ps1 / .sh / .bat`, you MUST `Bash` it in the same turn (`python
> <abs_path>`, `bash <abs_path>`, `powershell -File <abs_path>`). Ending a
> turn with an unexecuted helper script — or writing it then asking the
> user to run it in prose — is silent abandonment, not a deliverable. If
> you genuinely cannot execute it (sandbox missing, permission denied),
> call `AskUserQuestion` explicitly rather than burying the ask in
> assistant text. The harness has a StopPolicy that nudges you when this
> pattern is detected.

## 为什么是这个顺序

- **先 sandbox 再真改**：模型写 Python 改 JSON 经常出错（漏逗号、key 拼写错、`json.dumps(ensure_ascii=False)` 漏了导致中文乱码）。在副本上试 → 验证通过 → 才动用户文件。
- **必 FileVerify**：不要相信"我做完了"。每一步用机器可读断言确认。**user 看不到的 bug 比 user 看到的更糟糕**。
- **backup**：app 自动 reload 时如果你的写盘是半成品状态，app 可能拒绝打开。.bak/ 是回退保险。

## 关键 FileVerify 断言模板

| 场景 | 断言 |
|---|---|
| 改完文件还能被 app 打开 | `extracted_block_parses` / `regex_match` 检查前导 frontmatter |
| 关键字段存在 | `json_path_exists` |
| 新元素至少 N 个 | `json_path_count_min` |
| 没写错某个值 | `json_path_equals` |
| 没写入禁止内容（PII、绝对路径） | `regex_not_match` |
| 自定义逻辑 | `python_predicate` (e.g. `lambda d: any(e['type']=='image' for e in d['elements'])`) |

## 已知 app 速查表

| 文件 ext | 解析方式 | reload 行为 | 危险点 |
|---|---|---|---|
| `.excalidraw.md` (Obsidian Excalidraw) | YAML frontmatter + `%% ... %%` 之间的 JSON | mtime 监听，自动 reload | JSON 块外的 markdown 被破坏会让 plugin 报错；要走专属 `obsidian-excalidraw` skill |
| `.excalidraw` (纯) | 整文件 JSON | 同上 | dataURL 大、不要全部 print 出来 |
| `.ipynb` (Jupyter) | 顶层 JSON，cells 数组 | mtime 监听，会弹"外部修改"提示 | metadata.kernelspec 不能丢 |
| `.drawio` | XML | drawio-desktop 监 mtime 自动 reload | `<mxCell>` id 必须唯一 |
| `.canvas` (Obsidian Canvas) | JSON | mtime 监听 | nodes[].id / edges[].id 必须唯一 |
| `.md` (普通 markdown) | 纯文本 | mtime 监听，光标位置可能丢 | YAML frontmatter 缩进必须保持 |
| `.fig` (Figma) | 二进制 + 云端同步 | **文件层不通**，需 Figma plugin API | 走不通时降级到 ComputerUse |
| `.sketch` | zip 二进制 | 同上 | 同上 |

## 不要做

- ❌ **不要 `Write` 整个二进制 / 大文件** —— 用 Edit 做局部替换
- ❌ **不要假设原子写** —— app 可能在你写到一半时 reload 看到坏内容，最少要先写 `.tmp` 然后 `mv` 原子替换
- ❌ **不要忽略 UTF-8 BOM 和换行符** —— Windows app 经常对 CRLF/LF 敏感
- ❌ **不要并发写**：用户正在拖元素时你写盘 → 用户操作被覆盖。先 stat mtime，<2s 内有用户改动就稍等
- ❌ **遇到陌生格式不要瞎猜** —— Read 整个文件、查表，找不到表就开 sandbox 跑个 `python -c "import json; print(json.loads(...).keys())"` 探一下

## 代码骨架（模型可 copy-modify）

```python
# Step 1: Read 用户文件
content = Path("vault/notes/diagram.excalidraw.md").read_text(encoding="utf-8")

# Step 2: Sandbox 里改
# Bash(sandbox=true, command="""
#   python <<'EOF'
#   import json, re
#   text = open('/workspace/vault/notes/diagram.excalidraw.md', encoding='utf-8').read()
#   # ... 改 JSON 块 ...
#   open('/workspace/tmp/edited.md', 'w', encoding='utf-8').write(new_text)
#   EOF
# """)

# Step 3: FileVerify 副本
# FileVerify(target="tmp/edited.md", assertions=[
#   {"type": "extracted_block_parses", "between": ["%%", "%%"]},
#   {"type": "json_path_count_min", "between": ["%%", "%%"],
#    "path": "elements.*", "min": <old + new>},
# ])

# Step 4: 通过后 cp 到 backup，再 Edit 用户文件
# Bash(command="mkdir -p vault/.bak && cp vault/notes/diagram.excalidraw.md vault/.bak/diagram.{ts}.md")
# Edit(file_path=..., old_string=..., new_string=...)

# Step 5: FileVerify 真文件 + 等 reload
# FileVerify(target=user_file, assertions=[...])
```

## 配合其它 skill

- 遇到 PDF/图片读取需求 → 配合多模态 vision 直接喂渲染的 PNG
- 遇到 LaTeX 需要渲染 → 在 sandbox 里 `pip install` + 跑 `katex` 或 `matplotlib mathtext`
- 遇到 app 真不开放数据层（Wechat / 远程桌面 / 古董 Win 应用）→ 降级到 ComputerUse skill（如启用）
