# Experiments Log

> **用途**：记录实操实验的目标、过程、结果、暴露的 bug。每次跑 live smoke / live benchmark / behavior probe 都进来一条。
> **不放**：实施 changelog（去 implementation.md）、计划 / TODO（去 conversation.md）。

格式：顶部最新，往下变老。每条用三段：**目标 / 结果 / 暴露的 bug**。Bug 链回 conversation.md 里的任务 ID。

---

## P18-A 3/3 after CRLF + iter-cap + widened-detector fixes（2026-05-26，第 5 轮）

**目标**：第 4 轮暴露 3 个真坑——(a) WriteTool Windows LF→CRLF；(b) server
端 `or 8` 链把 0=unlimited 吃成 8，模型在第 8 轮被硬截；(c) shell_stuck +
handoff_phrase detector 漏掉中文 cmd.exe 错误 + 裸三引号代码块。修完跑 P18-A 看
能否真正 3/3。

**结果（deepseek-v4-pro，full-access，all P18-A）**：

- task_1（list sha→latex）：**PASS** 10.38s，无 flag
- task_2（append todo + 字节不变）：**PASS** 151.86s
  - WriteTool 改 `write_bytes` 后 frontmatter 字节完全一致，verifier 直接绿
  - mid-run `incomplete_plan` flag 触发并 nudge 一次（模型在中间一轮先列了 4 步
    计划但还没写文件就打算 end_turn，被 hook 推回去把第 3 步执行了）
- task_3（rename + 反链）：**PASS** 159.20s（独立 1/1 sweep：227.65s）
  - max_iterations bug 修后模型不再被 8 轮截断
  - 模型走完 Read → Bash rename → Edit×3，3 个 `[[old_note]]` 反链全改成
    `[[new_note]]`，note_c 字节不动
  - mid-run `incomplete_plan` flag 也触发并 nudge 了一次

**最终 P18-A pass 率：3/3 ✅**

**by_silent_handoff_flag = {incomplete_plan: [2, 3]}**：两个 PASS 的题都触发
了至少一次中间轮 nudge，说明 detector 在"真模型行为"上验证有效，不是只能跑 unit
test。无误报（task_1 干净一击就 PASS 没触发）。

**修复的 3 个真坑**：

1. **`agent/tools_v2/primitives.py` Write/Edit 用 `write_bytes`**
   - `path.write_text(s, encoding="utf-8")` 在 Windows 等价于
     `open(..., mode="w", newline=None)`，自动把 `\n` 翻成 `\r\n`
   - 改成 `path.write_bytes(s.encode("utf-8"))` 跨平台一致；Edit 第二次
     写盘也改了
   - 新 contract test：写带 `\n` 的内容，读回 bytes 断言无 `\r\n`
2. **`agent/ui/server.py:4211` + `config/app.yaml:137` 0=unlimited 被吃掉**
   - 原：`payload.get("max_iterations") or ((app_cfg.get("agent") or {}).get("max_iterations") or 8)`
   - bug：0 truthy-false，整条 `or` 链落到 `app.yaml::agent.max_iterations`
     （之前是 8），所以 harness 传 0 仍然被截 8
   - 修：新 helper `_resolve_max_iterations(*candidates)`，显式按 None 判断；
     app.yaml 默认改 0 并加注释解释 Claude-Code 模式
3. **detector 漏覆盖：中文 cmd 错 + 裸代码块 handoff**
   - `_SHELL_STUCK_PATTERNS` 加 6 条 zh-CN：`此时不应有 / 系统找不到指定的 /
     系统找不到文件 / 系统找不到路径 / 不是内部或外部命令 / 无法识别`
   - 新 `_BARE_FENCE_WITH_CMD_RE`：裸三引号后第一行像 shell command
     (`python foo.py`, `bash bar.sh`, `& foo.exe`, ...) 也算 fence
   - `_detect_handoff_phrase` 改成 fence-typed OR bare-cmd-fence
   - 两套 detector（hooks.py + harness_bench/base.py）都同步

**新 detector 上线：`_detect_incomplete_plan`**

- 触发条件：assistant_text 含 ≥3 个编号步骤（`1. ... 2. ... 3. ...`）或 ≥3 个
  `- [ ]` 未勾 checkbox + 含完成宣言（`done` / `已完成` / `完成` / `finished`
  ...）+ mutation tool 调用数（Write/Edit + mutating Bash mv/cp/rm/mkdir/touch/
  rename）少于 `planned * 0.6`
- precedence: `unexec > handoff > incomplete_plan > shell_stuck`
- 7 个新 unit test（4 个 detector 自身 + 2 个 policy 优先级 + 1 个 task_3-shape
  端到端）
- bench classifier `tests/harness_bench/base.py::classify_silent_handoff`
  同步加 `FLAG_INCOMPLETE_PLAN` + 同样的 regex 常量

**Hook 系统第 2 轮评估（首次有正例）**：

- ✅ 所有 unit test 95 通过（含新增 9 个）
- ✅ task_2 / task_3 都触发了 `incomplete_plan` nudge 并 PASS——nudge 真的把
  模型从 "我已经完成了" 状态扳回到继续执行剩余步骤
- ✅ task_1 没触发任何 flag（无误报）
- ⏭️ 下一轮：跑 P18-D 体检（确保新 detector 不会让 verifier 红灯漏报）+ 接着
  P18-B Excalidraw

---

## P18-A re-run after full-access boundary + silent_handoff hook（2026-05-26，第 4 轮）

**目标**：(a) 验证 P18.1.5 后 Edit/Write 在 full-access 模式下能写 workspace
外路径；(b) 验证 P18.1.4 silent-handoff StopPolicy 不会误触发，且 bench 后置
classifier 能给失败按 flag 归类。

**结果（deepseek-v4-pro，full-access，所有题）**：

- task_1（list sha→latex）：**PASS** 16.26s。Read 一次 → 文本直接输出 JSON，
  capability_scope=file_app（之前是 read-only mode 时也是 file_app，没回归）
- task_2（append todo + 字节不变）：**FAIL** 62.35s
  - 失败原因：`frontmatter bytes drifted (len_got=49, first_diff_offset=3)`
  - 根因：Windows 的 Python text-mode `Write` 把 `\n` 翻成 `\r\n`，前 3 字节
    `---` 没动但第 4 字节本应是 `\n`(0x0A) 变成 `\r\n`(0x0D 0x0A)
  - 模型 self-rate **`<self_confidence>uncertain</self_confidence>`** 并主动
    说"the Write tool on Windows can sometimes translate `\n` to `\r\n`...
    if the harness checks byte-identical, please re-run a byte diff"——**自我
    诚实**，不是 silent abandonment
  - `silent_handoff_flags = []`（hook 正确没误触发）
- task_3（rename + 反链）：**FAIL** 52.63s
  - 失败原因：3 个 `[[old_note]]` 反链仍在；rename 没真正做
  - tool_calls: `[Bash, Bash, Read×5, Bash, Edit, Edit]` ——做了 2 个 Edit 但
    没 `mv`，也没把 old_note.md 删掉
  - assistant_text 末尾是 plan 文本（"I'll start by exploring... The plan: 1.
    Rename ... 2. Edit ... 3. Edit ... 4. Leave..."）——**说了 plan 但没把
    plan 走完就 end_turn**，新失败模式
  - `silent_handoff_flags = []`（无 helper script、无 handoff phrase、Bash 没
    Windows shell 错——hook 正确没误触发，但这是 detector 漏覆盖的新模式）

**最终 P18-A pass 率：1/3（task_1 PASS）**

**Hook 系统首跑评估**：

- ✅ StopPolicy 注册成功（两处：主入口 + bg wake）
- ✅ 13 个 unit test 全过（detector 正负例 + plan-mode/read-only 关 +
  AskUserQuestion 跟随关）
- ✅ 后置 classifier 在 bench summary 多出 `by_silent_handoff_flag` 字段
- ✅ **无误报**：3 题失败 / 通过模式都没乱触发 flag
- ⚠️ **本轮模型行为没出现 silent-handoff 模式**——无法从正例验证 nudge 是否
  改善行为。Test_3 第 1 轮（2026-05-25）的"写 _tmp_rename_links.py 但不跑"
  模式没复现，模型这次直接选了别的失败路径

**两个 detector 覆盖不到的新失败模式**：

1. **Windows text-mode Write 把 LF→CRLF**（task_2 命中）
   - 不是模型问题，是 `WriteTool` 在 Windows 上 open with text mode encoded
     UTF-8 默认带 newline translation
   - 修向：`WriteTool` 强制 binary write 模式（`open(..., 'wb')` + bytes
     encode），或文档说明 byte verifier 应该把 CRLF→LF 规范化掉
2. **"plan 说了但 plan 没执行完就 end_turn"**（task_3 命中）
   - 跟 silent handoff 平级但更结构化：assistant_text 含编号 plan(`1.`, `2.`,
     `3.`)，但对应 tool call 缺失
   - 修向：新 detector `_detect_incomplete_plan(text, tool_calls)`——抓
     numbered list 步骤 vs tool call 数量

两个都列入 P18.7 后续 harness 迭代候选。

**与第 3 轮对比**：
- 第 3 轮 task_3 是 "写 helper script 不跑 + handoff phrase"
- 第 4 轮 task_3 是 "说了 plan 但 plan 没走完"
- 同一题不同跑暴露不同模式 —— 说明 detector 库需要持续扩，单个 hook 治不
  动模型所有失败方式

---

## P18-A task_3 re-run after no-hard-cap + no_implicit_waits（2026-05-26，第 3 轮）

**目标**：验证（a）取消 MAX_ITERATIONS 硬上限后模型是不是真的能用满 budget，
（b）system prompt 新加的 `<no_implicit_waits>` 块是不是能治 silent-script-abandonment。

**改动（同轮）**：
- subagent preset + bg wake + harness_bench 全部 `max_iterations=0`（unlimited）
- system prompt 加 `<no_implicit_waits>`：要求模型对"要用户做事"的场景必须调
  AskUserQuestion，禁止"写脚本叫用户跑然后结束 turn"
- UI 加独立红色 Stop 按钮（不替 Send→Stop overload，并存）

**结果**：
- task_3 **仍 FAIL**（69.24s，9 tool call：Bash/Read×4/Grep/Bash×3）
- `stop_reason=end_turn`——确认是**模型自愿停**，不是 server 硬截
- 模型 assistant_text 还说"the Python fallback that would have done the work
  was cut off by the iteration limit"——这是**幻觉**，已经没有 iter limit 了
- 仍然写了完整的 `import os, re; vault = r'...'; ...` Python snippet 让用户跑
- 仍然 `<self_confidence>fail</self_confidence>`

**结论**：
- ✅ 取消硬上限的代码改动正确（loop 不再截，模型自己决定停）
- ❌ 单条 prompt 规则**治不动** DeepSeek V4-Pro 的"写脚本不跑"行为先验
- 需要更强的干预层级（按强弱）：
  1. 在 SKILL 或 file-app-workflow context 里加更短更硬的 inline 规则
  2. 检测 assistant_text 末尾出现 "```python ... ```" 但 no Bash python call → harness 自动 retry 一轮，feedback "you wrote a script but didn't run it, run it now"
  3. 换模型（GPT-5.4 / GPT-5.5 / Claude）做对比
- 决定推后到 P18.7+ harness 迭代环节统一处理。本轮 prompt 改动留着，不回滚——
  至少对其它弱先验场景有效（task_1/2 没回归，下一轮全集跑能看出来）

---

## P18-A smoke vs DeepSeek V4（2026-05-26，第 2 轮 review-followup）

**目标**：reviewer 抓的 4 must-fix + 1 nice-to-have 修完后，重跑 P18-D 体检
+ P18-A 三题，确认（a）byte-level task_2 verifier 不漏判（b）Tier 命名改 P18-A/B/C/D
后 summary 一致（c）非 verifier-相关的失败模式是 model 真实问题，不是 harness 误判。

**结果（deepseek-v4-pro，full-access，本轮代码）**：

- P18-D（13/14 verifier 红灯体检）：**2/2 PASS**（0.0s + 0.0s）
- P18-A task_1（list sha→latex）：**PASS** 10.05s（lenient `_PAIR_RE` 提取仍正确）
- P18-A task_2（append todo + 字节不变）：**初跑 FAIL → 提 MAX_ITERATIONS=8→12 后 PASS** 115.55s
  - 初跑（iter=8）：模型 burn 完 9 tool call（Read/Bash×4/Write/Bash×3/Write）都在
    debug Windows cmd.exe heredoc，**iter 限额耗尽前未触发成功 Write**
  - 模型自己写了 `_apply_patch.py` helper + 老实输出 `<self_confidence>fail</self_confidence>`
  - 提 iter 到 12 后第二跑 PASS：8 tool call，frontmatter+## Notes 字节级一致
- P18-A task_3（rename + 反链）：**FAIL** 85.63s（recurring silent-script-abandonment）
  - 同样 helper script 写了没执行的模式（第 1 轮就有，本轮再现）

**最终 P18-A pass 率：2/3（task_1/2 PASS，task_3 known harness gap）**

**本轮新发现**：

1. **byte-level verifier 改对了**——本轮模型变体不同，第 1 轮 task_2 用文本 substring 假阳过；
   本轮一旦没真写文件，mtime 不进 + byte 不一致就立刻 fail loud。reviewer 的"假阳性"判断正确。
2. **MAX_ITERATIONS=8 对 Windows + 多 tool 任务太紧**——模型 debug heredoc 容易爆 budget。
   task_2 提到 12 后稳定。其它题需要时再单独调。
3. **silent-script-abandonment 是 recurring harness gap**（task_2 第 1 跑 + task_3 两轮都中招）
   - 模型可靠产出 helper script + `<self_confidence>fail</self_confidence>`，但不自驱执行
   - 修向：SKILL.md 增加 "写完脚本立即 `python script.py` 执行" 的硬规则；
     或在 prompt prefix 注入 "如果你写了 .py 脚本，必须在同一轮内执行它"
   - 暂未单开 task，留到 P18.7+ harness 迭代

**与第 1 轮的差异**：
- 第 1 轮 task_2 PASS 是文本 substring 假阳；byte-level 改后才是真 PASS
- 第 1 轮 task_3 FAIL（cmd.exe heredoc）/ 本轮 task_3 FAIL（同样的 silent script 模式）—
  两次都死在 "写脚本不执行"，确认是 reproducible 而不是 one-off

---

## P18 Tier A smoke vs DeepSeek V4（2026-05-25，第 1 轮，**SUPERSEDED**）

> ⚠️ 已被上方第 2 轮取代。task_2 当时的 PASS 是文本 substring 假阳（reviewer 发现），
> task_3 的 FAIL 现象一致。保留作为修复前后的对比记录。

**目标**：harness_bench skeleton + Tier A 3 题首次端到端跑通真模型，
验证 prompt 措辞 / verifier 严格度 / 工具暴露是否合理。
是 P18.6 baseline run 的前哨——3 题先于全集发现问题，避免返工。

**结果（deepseek-v4-pro，full-access 模式）**：
- task_1（list sha→latex）：**FAIL→PASS**（修两次后）
  - 第 1 跑：`capability_scope=desktop`，manifest 只有 `DesktopAct/Observe/Verify/AskUserQuestion`，
    模型诚实回 "no file-reading tool available"。**8s 直接 fail**
  - 第 2 跑（prompt 加 "Obsidian note" 关键词）：`scope=file_app`，
    模型 Read → 输出完美 JSON，但 verifier 死于 `json.loads` 严格——
    模型输出 `\\int \\frac \\sqrt`（**裸反斜杠是非法 JSON escape**）
  - 第 3 次（无需 re-call 模型，直接 replay 旧 outcome）：
    verifier 换成 key-value regex 容错提取，pass
  - 耗时：10.65s + 8.02s + 0 (replay)；2 次真模型调用
- task_2（append todo 到 ## Tasks）：**PASS** 第 1 跑
  - `scope=artifact`，full toolset，8 次 tool call（Read/Bash×6/Write）
  - 110.94s
- task_3（rename + 改反链）：**FAIL** 第 1 跑（保留作为发现）
  - `scope=file_app`，13 次 tool call，**模型踩了 Windows cmd.exe heredoc 坑**
  - rename 成功（`mv old_note.md new_note.md` 通过 Bash）
  - 改反链：只 Edit 改了 note_a.md 中 1 处。然后想用
    `python << 'PYEOF'` 批改剩余文件——cmd.exe 不支持 heredoc，失败
  - Fallback：写了 helper 脚本 `_tmp_rename_links.py` 但**未执行**
  - 模型老实输出 `<self_confidence>fail</self_confidence>` 并要求用户手跑脚本
  - 90.17s

**最终 Tier A pass 率：2/3（task_1/2 pass，task_3 fail）**

**暴露的 bug / harness 发现**：

1. **harness routing gap**（影响所有"纯 file-read"型 prompt，task_1 命中）
   - `_resolve_turn_skill_and_tools` 找不到 skill 又无 image，落 `select_desktop_capabilities`
   - 桌面回退作用域不含 Read。pure "Read the file at <path>" prompt 必然失败
   - 临时绕：prompt 加 "Obsidian note" / "vault" 等关键词触发 file-app-workflow
   - 长期：考虑给 desktop 兜底 scope 加 Read/Glob（或在路径检测到本地 file 时升级 scope）

2. **Windows heredoc 在 Bash tool 里失败**（task_3 命中）
   - `feedback_docs_convention.md`（P14.6.16-G）已有 Windows Bash tips 但模型未生效
   - 模型生成 helper 脚本却没自驱执行（无 `python script.py` 后续）→
     **silent task abandonment**——`<self_confidence>fail</self_confidence>` 救了一命
   - harness 应该：(a) 在 SKILL 里更强调"写完脚本立即 `python script.py` 执行"；
     (b) verifier 看到"helper script created but never invoked" 模式应直接红灯
     （已隐式：FileVerify/oracle 走的是终态校验，task_3 反链未改自然 fail）

3. **verifier JSON parser 太严**（task_1 命中）
   - `json.loads` 拒收 `\int \frac` 等 LaTeX 风格的"非法 escape"
   - 改用 `_PAIR_RE = re.compile(r'"([a-f0-9]{8,})":\s*"((?:[^"\\\\]|\\\\.)*)"')` 
     直接抓 key-value，绕过 JSON 严格性
   - 教训：模型生成的"JSON"经常技术上无效（裸 backslash / 尾逗号 / 注释），
     verifier 不应依赖 strict parser

**链回 task ID**：
- 此条 smoke 完成于 P18.3 验收后；3 个发现都属"harness 层"，
  不影响 P18.4/P18.5 推进
- "task_3 反链漏 silent fail"建议在 P18.7+ harness 迭代里做 fix（暂未开 task）

---

## P13.2.3 LaTeX→SVG → files[] dataURL 闭环（2026-05-19，第 12 轮）

**目标**：把 SKILL.md 的"插 LaTeX 公式"小节从伪 katex 骨架换成真实可跑的
matplotlib mathtext recipe，让模型不只是写 `customData.latex_source`，
而是真的渲出 SVG / base64 编码 / 塞 `files[fileId].dataURL`，
使用户在 Obsidian 里看到的是公式像素而不是破损图占位框。
prompt 也从公式 (5) 改成公式 (6)(7) 的完整推导。

**结果（gpt-5.5，profile override，unattended）**：
- 跑时 214s，32 次 tool call（含 9 次末尾误用 FileVerify）
- target 文件 35851 → **136464 B** (3.8×)，elements 55 → **90** (+35)，files 5 → **15** (+10 SVG dataURL)
- post-flight verdict：`latex_image_count=16`，**`with_svg=15`**，`missing_svg=1`
  - 那 1 missing 是 round 11 残留的公式 (5) 占位（gpt-5.5 没碰；用户说已自己手写）
  - 15 张新 image 每个 `files[fid].dataURL` 都是 `data:image/svg+xml;base64,...` 开头，
    长度 4-22 KB，覆盖 TLM 完整推导链：
    `R_T = R_{c,in}+R_s+R_{c,out}` → `dR_s = R_{shs}/(2πx)dx` → `R_s` 积分闭式 →
    Bessel ODE `d²V/dr² + 1/r dV/dr − V/L_t² = 0` →
    `V(r) = A I_0(r/L_t)` / `V(r) = B K_0(r/L_t)` → `R_{c,in}` / `R_{c,out}` →
    总式 → `r ≫ 4 L_t` 渐近 → 简化形式
- 用户视角验收（待人工查 Obsidian）：predicted ✅ 能看到 15 张公式 SVG

**新增 / 修改**：
- `requirements.txt` 加 `matplotlib>=3.7`
- `skills/obsidian-excalidraw/SKILL.md` "插 LaTeX 公式" 整段重写：完整 matplotlib 配方 +
  size 换算 + 6 条 anti-pattern + 公式后必跑的 FileVerify dataURL 检查模板
- `tests/unit/test_skill_latex_to_svg_recipe.py` 6 case 新文件（matplotlib round-trip +
  SKILL.md 内容断言 + dataURL 长度阈值断言），全过
- `tests/p13_obsidian_live_smoke/run_explain_formula_smoke.py`：
  - prompt 改成 (6)+(7) 完整推导
  - `_summarise_file_change` 增加 compressed-json 解码（lz-string）+ 每个 latex image
    的 dataURL 详细 verdict（is_svg_b64 / len / count_with_svg / count_missing_svg）

**暴露的 bug / 残留**：
- ⚠️ gpt-5.5 末尾 9 次 FileVerify 全 ERR：模型瞎编 assertion type
  （`"contains_text as unsupported? no"`），而且 target 指错到了自己写的 scratch
  `.py` 而非 vault 文件。说明 FileVerify schema discoverability 不够 ——
  模型不知道有哪些合法 type，也不知道应该 verify 哪个文件。
  (新 bug，未建任务；不影响本轮主功能。后续可加 FileVerify error message 提示
  "valid assertion types are: ..."，并在错误返回里列出。)
- ⚠️ Scratch `insert_formula67_excalidraw.py` 又被留在项目根。已 rm。
  这是反复出现的模式 —— skill 应该说清"sandbox 的 .py 写完即丢，不要落在
  host CWD"。

---

## P13.1 obsidian-excalidraw live smoke 系列（2026-05-16 ~ 2026-05-18）

**目标**：让 doubao-seed-2.0-code 在零先验的情况下，给一个用 Obsidian 写的 Excalidraw 画板插入一段公式 (5) 的推导。Prompt 只给论文题目，**不给** vault 路径、画板文件名、读 PDF 方法、渲染 LaTeX 方法。Runner 监听 `**/*.excalidraw.md`，post-flight 看 hash 变没变 + JSON 是否仍然 parse。

### 第 1 轮（20260423）—— 全员 0 改动

- **结果**：4 个 tool call，1300s+，0 file changed。
- **暴露**：
  - 任何 Bash 都进人工审批 → 300s 超时。
  - `AskUserQuestion` 走 stub 想法被否决（应该让真人答或不答）。
  - Smoke runner 干脆把 vault 路径塞进 prompt → 不该这么做，应该让 agent 自己找。

### 第 2 轮（20260516）—— 看见 stale `demo_vault/` 就上当

- **修复带入**：#91 Bash low-risk auto-approve。
- **结果**：8 个 tool call，1152s，0 file changed。
- **暴露**：
  - **Bug 1**：上一轮残留的 `demo_vault/` 没清，agent `dir` 一看就当成真 vault。→ 任务 #94（skill 硬性禁止），runner 也加 pre-flight scrub。
  - **Bug 2**：`_SANDBOX_PREFERRED_COMMAND_PATTERN` 把任何 `python script.py` 都送 Docker，host 文件一字不改。→ 任务 #95。

### 第 3 轮（20260518 08:03）—— Skill 教会找 vault 后撞上 Bug 3

- **修复带入**：#94 / #95 / runner scrub。
- **结果**：4 个 tool call，1082s，0 file changed。
- **暴露**：
  - **Bug 3**：agent 正确地用 `python -c "...; ... obsidian.json ..."` 想读配置，但 `_SHELL_CONTROL_PATTERN` 把 Python 字符串里的 `;` 当 shell 拼接 → medium-risk → 人工审批 300s 超时。→ 任务 #96。

### 第 4 轮（20260518 08:43）—— Quote-aware 修了之后撞两个新坑

- **修复带入**：#96。
- **结果**：5 个 tool call，1272s，0 file changed。
- **暴露**：
  - **Bug 4**：vault 在 `workspace_root` 外，`Glob` / `Read` 拒绝放行（PermissionError）。→ 任务 #98。
  - **Bug 5**：`python -c "... glob.glob('**/*.md') ..."` 触发 `_HIGH_RISK_BASH_PATTERN`，因为 `*` 在 Python 字符串里被当 shell 通配。→ 任务 #97。

### 第 5 轮（20260518 10:37）—— Infra 全打通，撞领域天花板

- **修复带入**：#97（high_risk 拆 content/syntax）+ #98（restricted 模式只 gate 写，读全放）。
- **结果**：**17 个 tool call，241s，无超时**。但 vault 监测 hash 全相同 ⇒ 任务实际未完成。
- **完成度**：
  - ✅ 自己 `python -c` 读 obsidian.json 找到 vault
  - ✅ 自己探索 vault 结构 + `Excalidraw/` 子目录
  - ✅ pymupdf 解析 PDF，定位公式 (5): `Lt = √(ρc / Rshs)`
  - ❌ 挑错画板（按 mtime 取最新而非按论文标题搜）
  - ❌ 解不开 Obsidian Excalidraw 的 pako 压缩块（多行 base64 + raw deflate 细节没掌握）
  - ❌ 最后停在"让我尝试一个不同的方法..."就 `end_turn`，没出验收摘要
- **暴露**（都是通用 agent 行为缺陷，不是 Excalidraw 专属）：
  - **Bug 6**：`hooks.py:_INTENT_PATTERN` 漏了 `让我X` / `我先X` / `我想X` 这类最常见的中文宣告式，nudge 没触发。→ 任务 #99。
  - **Bug 7**：`final_guard` 只抓过去完成态（`created / wrote / 已修改`），抓不到未来意图（`将 / 打算 / will`）兑现失败。→ 任务 #100。
  - **Bug 8**：用户明确说"标题是《...》"，agent 仍按 mtime 选文件。需要 system-prompt 级的 "follow named entities, never sort by mtime" 规则。→ 任务 #101。

### 第 6 轮（20260518 19:43）—— #99 / #100 / #101 落地后

- **修复带入**：#99（intent pattern 扩 `让我X` / `我先X` / `我打算` / `我准备` / `我想X`）+ #100（final_guard 增 `_FUTURE_INTENT_CLAIM_PATTERN` 抓未来意图）+ #101（base prompt 加"按命名实体优先 Glob/Grep，不要按 mtime"规则）。
- **结果**：**22 个 tool call，821s**，stop_reason `end_turn`，target 文件大小 29460→30044（涨 580 bytes 但 `json_parses=false`），post-flight 跟踪的 7 个 `.excalidraw.md` 均无变化。
- **进展**：
  - ✅ **#101 见效**：模型这次直接按论文标题搜（`*Comparative Evaluation*`），定位到 `文献阅读/SD接触/接触电阻测试/A Comparative Evaluation ... A Review.md`，而非第 5 轮的 mtime-newest `Drawing 2026-05-16 ...`。`base_agent_prompt` 的"按命名实体先 Glob/Grep"规则有效。
  - ✅ #91 / #94 / #95 / #96 / #97 / #98 全部仍工作。
- **未触发**（按设计）：#99 / #100 nudge 均未 fire — 模型 22 个 tool call 几乎每条 assistant 文本后都接 tool_use，`intent_without_action` 与 `final_guard` 都只在"纯文本无 tool"时触发，所以没插嘴。
- **暴露**（**两个新 Bash gating bug**）：
  - **Bug 9** → 任务 **#102**：`cd "...vault" ; dir /s /b "*Comparative Evaluation*"` 被 `_HIGH_RISK_BASH_SYNTAX_PATTERN` 的 `/s` alternative 命中（本意是 PowerShell `-recurse`-style 标记），但 Windows `dir /s` 是只读列目录。300s 审批超时。
  - **Bug 10** → 任务 **#103**：`cd "D:\D\python编程\Agent-building" ; python decompress_excalidraw.py` 因为 `;` shell_control 被评 medium-risk，300s 审批超时。这是 cwd-then-run 惯用语，不是多命令拼接。
- **残留旧坑**（与上一轮同）：
  - 模型仍解不开 Obsidian Excalidraw 的 pako `compressed-json`，只能往 `.md` 外层写一些纯文本（解释了 580 bytes 涨幅 + JSON 块仍坏掉）。这是 skill / 领域知识层问题，留 P13.2 处理。
  - 没出验收摘要 — `acceptance_summary` policy 也未 fire（最后两条 Write/Read 都是 tool call，没纯文本结尾），由于 stop_reason 自然 end_turn，policy 没机会插话。

### 第 7 轮（20260519 08:15）—— #102 / #103 / #104 / #105 落地后，doubao-code

- **修复带入**：#102（`/s` 不再触发 high_risk）+ #103（`cd "..." ; cmd` 不再触发 shell_control）+ #104（unattended 模式 medium/high 立即 deny，不再等 300s）+ #105（SKILL.md 加 `compressed-json` 完整 read/write recipe）。runner 默认 `unattended=true` + 接 `--profile` arg。
- **结果**：**22 个 tool call，161s**（vs 第 6 轮 821s，**5x 加速**），stop_reason `end_turn`，target 文件大小 30044（不变），post-flight 跟踪的 7 个 `.excalidraw.md` 仍均无变化。
- **进展**：
  - ✅ **#104 完胜**：trace 里所有 medium/high Bash 都立即被 unattended-deny（emit `approval_auto_deny` 事件），模型秒级 pivot 写新脚本，再没有 300s 死等。整局时间砍 80%。
  - ✅ **#102 + #103 工作**：trace 里再没出现 round 6 时 `dir /s` / `cd ... ;` 触发的 shell_control / high_risk reasons。
  - ✅ **#105 触达模型**：agent 写出了名为 `decompress_and_read.py` / `read_and_decompress.py` 的脚本，里面**正确用了 `zlib.decompress(compressed, -15)`** —— 正是 SKILL.md 现在教的 raw-deflate 解法（之前模型只知道 `zlib.decompress(raw)` 默认 wbits）。
- **未触发**（按设计）：#99 / #100 nudge 仍未 fire — 模型 22 tool call 每条 assistant 文本后都接 tool_use。
- **仍未过关**（**两个新问题**）：
  - **Bug 11**：模型用了正确的 `-15` wbits，但 decompression 仍然 `Error -3 invalid block type` —— 大概率是它的 base64 抽取逻辑（fence 切割）有 off-by-one，把 fence 反引号或行首空格塞进了 base64 输入。SKILL.md 给的 recipe 里 `re.sub(r"\\s+", "", m.group(1))` 是关键一步，模型可能没照搬。需要在 SKILL.md 里加一段**调试 checklist**："如果 Error -3 / Error -5，先 print base64 前 80 字符确认没 fence 边界 / BOM / 中文字符渗入"。
  - **Bug 12**：runner 把 `max_iterations` 设为 20，模型还差 1-2 个 Write 就完工，最后 2 个 Write 被 loop 拒了"Iteration limit reached"。需要根据真实任务复杂度调一下（公平起见两个模型都要够用）。
- **残留旧坑**：模型解压失败后没回头排查，反而开始尝试别的方法 — 这个层面就是模型能力 + skill 教学不到位的混合问题。

### 第 8 轮（20260519 09:40 + 09:44）—— gpt-5.5 初跑暴露 #106；hint 加上去后跑通

**8a（09:40）**：第一次让 gpt-5.5 跑，仅 **15s / 1 tool call** 就 end_turn。模型拿到 generic "denied by the user" 文本后，没有任何 retry，直接问真人。trace 揭示 `make_approval_hook` 把 approver 返回的 False 转成固定字符串，丢掉了 #104 unattended 路径填的 risk reasons。**Bug 13 → 任务 #106**。

**修复带入**：#106 —— approver 返回类型扩成 `True | False | str`，str 直接成为 tool_result content。unattended 路径按 `reasons_list` 写 retry hint（`shell_control` → "rewrite without ';' / '&&' / heredoc"; `high_risk_pattern` → "drop bare wildcards / recurse flags"; `sandbox_preferred` → "retry with sandbox=true"; `mutating_or_system_command` → "use Write/Edit").

**8b（09:44）**：重跑 gpt-5.5 —— **140s / 23 tool calls**。模型这次看到 hint 就改命令形式（`<<heredoc` 改成 `python -c "..."` one-shot），不再问人。但 base64 解码全部 `Invalid base64-encoded string: number of data characters cannot be 1 more than a multiple of 4`，`zlib.decompress(raw, -15)` 全部 `Error -3 invalid block type`。**模型严格按 SKILL.md 给的 pako/zlib 配方走，配方本身就是错的**。

**Bug 14 → 任务 #107**：实测 target file 末尾是 `===`（三个等号），前 3 字符是 `N4K` —— 这是 **lz-string** 的 base64 变种（`LZString.compressToBase64`），不是 pako/zlib。Python 用 `pip install lzstring` 然后 `lzstring.LZString().decompressFromBase64(body)` 一次就解出。

### 第 9 轮（20260519 10:12）—— 修正后的 lz-string 配方 + 模型自己写脚本

- **修复带入**：#107 SKILL.md 配方全改成 lz-string + `requirements.txt` 加 `lzstring>=1.0.4` + `max_iterations` 升到 35。
- **结果**：30 tool call，335s。模型写了 `decompress_excalidraw.py` / `process_excalidraw.py` / `add_eq5_derivation.py`（全部带 `import lzstring`），路径正确，但 `python script.py` 全部 `ModuleNotFoundError: No module named 'lzstring'`。
- **Bug 15 → 任务 #108**：模型 `python script.py` 用的是系统 PATH 第一个 python（这台机器是 `miniforge3` base），不是 `.venv`。lzstring 装在 `.venv/Lib/site-packages/`，base 装不到。`pip install lzstring` 被 #104 sandbox-prefer 自动拒。

### 第 10 轮（20260519 10:21）—— 真正第一次打通 compressed-json round-trip

- **修复带入**：#108 BashTool 子进程 env 把 `.venv/Scripts` 顶到 PATH 最前 + 设 `VIRTUAL_ENV`。
- **结果**：**26 tool call，393s**。模型脚本现在 `import lzstring` 成功。多次 Write + Edit 后，**target file 30588 → 32509 字节**（涨 1921 字节）。
- **post-flight 验证**（用我们自己的 lzstring round-trip）：
  - ✅ Fence 完整保留
  - ✅ `lzstring.LZString().decompressFromBase64(body)` 解码干净
  - ✅ JSON parse 干净，**55 个 elements**（原 54 + 1 新增）
  - ✅ 新元素 `id=img_a68e6c2e464e`，`type=image`，`x=242.81`，`y=-4511.23`，`width=550`，`height=850`
  - ✅ **`customData.latex_source` 含 `【公式 (5) 详细推导：比接触电阻提取】` + 推导公式** —— 模型按 skill 教的 latex_source 约定填了
  - 🟡 `status=pending`，`files{}` 中**没有对应 `fileId=c38f26bbebf94705aeb5d27440358437` 的 dataURL` —— 模型没把 LaTeX 渲染成 SVG 再 embed。Excalidraw 打开会显示破损图片占位。
- 评价：**核心 read/modify/write 闭环全通过**。剩下"LaTeX → SVG → 嵌进 `files[]`"是独立的渲染步骤，模型没完成。

### 第 11 轮（20260519 10:51）—— gpt-5.5 同样配置

- **结果**：27 tool call，**186s**。target file 32509 → 35851 字节（涨 3342）。
- **post-flight**：
  - ✅ lz-string round-trip 完整通过
  - ✅ JSON parse 通过，elements 仍 55
  - 🟡 gpt-5.5 **复用了 round 10 doubao 留下的 `img_a68e6c2e464e` 元素 id + fileId**，但把 `customData.latex_source` 重写得更完整（增补 `L_t=√(ρc/Rshs) ⇔ ρc=Rshs·Lt²` 的双向等价 + TLM 微分模型推导）
  - ✅ **额外用 FileVerify 9 次做 round-trip 验证** —— gpt-5.5 走的流程更接近"先验证再宣称完成"，符合 file-app-workflow skill 教的范式
  - 🟡 `files{}` 仍空（同 round 10，没渲 SVG）
- 评价：相比 doubao，gpt-5.5 显著更省 tool call（27 vs 26 几乎一致，但 time 减半），且主动调 FileVerify。同样**核心闭环已通**，差最后渲染一步。

### 当前状态（截至 2026-05-19 11:00）

**Infra 全栈打通**（#91 / #94 / #95 / #96 / #97 / #98 / #99 / #100 / #101 / #102 / #103 / #104 / #105→#107 / #106 / #108）：
- Bash 默认放行 / shell-control 引号感知 + cwd-prefix 豁免 / high-risk 拆分 + `/s` 不误抓 / sandbox 不误抓 / workspace 只 gate 写
- intent 中文补全 / final_guard 抓未来兑现 / base prompt 命名实体优先
- unattended 模式秒拒（无 approver 不等 300s）+ approver 回 reason string + 按 risk reasons 写 retry hint
- Bash 子进程 PATH 顶 `.venv/Scripts` → `python script.py` 走项目 venv
- SKILL.md 教 **lz-string**（不是 pako/zlib）+ 调试 checklist + FileVerify 模板

**真实任务**：
- 第 10 / 11 轮的 target file 落地都通过 lzstring round-trip 检验 + JSON parse 检验，elements 增加了 1 个并带正确的 customData.latex_source。
- **唯一未完成**：LaTeX → SVG 渲染 + embed 到 `files{}` 的 dataURL。两个模型都没走这一步。这是独立的 skill 内容（应该是 `tex_to_svg` recipe），不是 infra 缺。

**Smoke runner 验收口径目前的盲区**：runner 的 `target_md_verdict` 只解析 plain `%% ... %%` 块，不识别 ```` ```compressed-json ```` 围栏，所以 `json_parses=false`。但用 lz-string 实测 round 10/11 后的 target file 是 round-trippable 的。Runner 验收逻辑要更新成"先试 compressed-json，再试 plain"。

---

## P14.6.16-REF — Claude 自我参照运行 T1（2026-05-24）

**目标**：去掉所有 obsidian_* 专项工具后，Claude 本人（meta-tier only）能否完成 T1（公式 6/7 推导写入真 canvas）。建立人工 baseline 用于对比模型。

**配置**：mirror 关闭（写真 vault，留 backup），仅可用 Glob/Read/Write/Edit/Bash + .venv 里的 lzstring/pdfplumber/matplotlib。

**结果**：完成，10 个 tool call，~5 min。
- Glob × 1（定位 canvas md）
- Bash × 8（snapshot、解 compressed-json、pdfplumber 抽 page 4 坐标、推 latex、回写）
- Write × 1（一次性把所有逻辑写进 `tests/_tmp_claude_ref_apply.py`）

**产出**：6 个 latex element（含 (6)/(7) 标签）+ 1 frame "公式 (6)(7) 推导" + groupId 串联 + viewport 对焦。L1 + L2 oracle 全绿，task gate pass。

**暴露的真 bug（GUI 验证后才发现）**：
1. **6 个 element 的 fileId 全是随机 `lf_<random>`**，不是 `sha1(latex_source)`。oracle 没抓到。
2. **Embedded Files 节里完全没有 latex 的 sha1 行**（只有 PDF 页映射）。我以为 `customData.latex_source` 是 fallback，实际 plugin 必须先在 fileId / Embedded Files 里 resolve 才会跑 KaTeX。链路断 → 6 个图全是空占位（用户截图证实）。
3. **加了一个蓝边 frame**（"公式 (6)(7) 推导"），多余。用户反馈：要分组用 groupId 就够，frame 是给截图加可视边框用的。

**修复**：
- `tests/_tmp_fix_latex_link.py` —— 把 fileId 改成 sha1(latex)，files{} 键同步，Embedded Files 追加 `<sha1>: $$<latex>$$`。reload 后 file size 35663 → 37813（plugin 把 KaTeX SVG 回填到 dataURL，每个 ~350 字节 × 6 ≈ 2150）。这个 size delta 是**渲染成功的硬证据**。
- `tests/_tmp_remove_frame.py` —— frame `isDeleted=true`，6 child `frameId=null`（保留 groupIds），重算 viewport 对焦 group bbox。

**关键 takeaway**：
1. oracle 对 fileId / sha1 / Embedded Files 三处一致性**没断**，所以 Claude 自己跑也假绿，模型跑就更假绿。
2. **唯一可信的渲染验证 = 文件 size delta（reload 后 dataURL 应从 0 涨到 >500 字节/element）**。这是手段 #4「副作用代理信号」的具体应用。
3. SKILL.md 现有的 "SHA1 → Embedded Files" 提示**不够强**，没说三处必须等同，也没强调 frame 多余。

---

## ENOTDIR vault 加载大调查（2026-05-25）

**触发**：用户报 Obsidian 启动报 `Error: ENOTDIR: not a directory, scandir 'D:\D\scientific research vault'`，但 Python `os.scandir` / `os.walk` 全部健康。仓库内容完整（72 顶层条目 + .obsidian/）。

**调查路径**：
1. ❌ NTFS 目录损坏 → chkdsk 要 admin，跳过；用 Python 多种方式验证目录可读
2. ❌ 路径含空格 → 重命名 `scientific research vault` → `scientific_research_vault_test`，仍 errored
3. ❌ 插件锅 → 把 `.obsidian/community-plugins.json` 改 `[]`，仍 errored
4. ❌ `.obsidian/` 配置损坏 → 把 `.obsidian/` 整个改名让 Obsidian 重建，仍 errored
5. ✅ **vault 内容某项触发** → 系统二分（每轮：移空 vault → 部分条目放回 → 探针看 `workspace.json` mtime 是否被 Obsidian 重写）

**二分结果**：
- 空 vault → LOADED
- CJK 子目录（文献阅读 / 论文阅读）放回 → LOADED
- 35 项放回 → LOADED
- 17 项放回 → LOADED
- 50 项放回 → LOADED
- **51 项放回 → ERRORED**
- 60 项放回 → ERRORED
- 单独任一项（67 / 68）→ LOADED

**结论**：libuv `uv_fs_scandir`（用 NtQueryDirectoryFile）在 Windows 11 + Obsidian 1.12.7 的 buffer 上限大约是 **50 个 entry / 单目录**。超过就整目录返回 ENOTDIR。

**触发原因**：vault 根有 **60 个 `Pasted Image YYYYMMDDhhmmss_NNN.svg`**，是历次 Excalidraw 粘贴图片时 plugin 的 attachment folder 配到了 root。+ 7 PDF + .canvas/.excalidrawlib/.txt + 2 CJK 目录 + .obsidian → 73 entry，超阈值。

**修复尝试**：
- 把 60 SVG 移到 `attachments/`（单目录 57 个）→ 仍 ERRORED（subdir 同样受限）
- 拆成 `attachments/2026-05-19/`（15）+ `2026-05-20/`（15）+ `2026-05-21/`（27）→ **仍 ERRORED**

**奇怪现象**：拆 subdir 后所有目录都在阈值下，仍报错。1 个 SVG 在 attachments/ → LOADED。这说明**阈值不是单纯单目录计数**，可能是总文件数 + 某种 SVG-specific 索引行为的组合，需要进一步实验（未做）。

**临时收尾**：保留 attachments/ + 3 subdir 结构（symptom 仍在但用户接受，因为可以打开 vault 后通过禁用 restricted-mode 让插件回来）。SVG 用户可以以后整理。

**探针工具**（可复用）：
```python
# tests/_tmp_probe_obsidian.py
def probe(wait_s=5.0) -> str:
    """看 .obsidian/workspace.json 是否被 Obsidian 创建或重写。"""
    ws = Path(vault) / ".obsidian" / "workspace.json"
    before = ws.stat().st_mtime if ws.exists() else 0
    time.sleep(wait_s)
    after = ws.stat().st_mtime if ws.exists() else 0
    return "loaded" if after > before + 0.5 else "errored"
```

**副发现（restricted mode）**：vault 重建 `.obsidian/` 后，Obsidian 1.0+ 默认进入 restricted mode，社区插件不加载 → 用户看到「插件都没了，包括 excalidraw」。修复：Settings → Community plugins → "Turn on community plugins"。

---

## 渲染验证探针 — file size delta 模式（2026-05-25）

**问题**：oracle 只能验数据层（element 存在 / 字段对），验不了 plugin 实际渲染。

**实测发现**：在 P14.6.16-REF 修复 fileId↔sha1 链路后：
- 修复前文件 36622 bytes，6 image 占位空
- `_tmp_fix_latex_link.py` 写完 35663 bytes（去掉冗余字段稍微缩）
- 用户在 Obsidian 内 Command Palette "Open as markdown → Open as Excalidraw" 触发 reload
- 再读文件 → **37813 bytes**（涨 2150）

**解释**：plugin reload 后 KaTeX 渲染每个 latex，把生成的 SVG base64 dataURL 回填到 `files[fileId].dataURL`。6 个 element × ~350 bytes/dataURL ≈ 2150。**这个 size delta 是 plugin 真渲染过的硬证据**，比检查"element 字段对不对"靠谱得多。

**抽象出来的模式**：`probe_app_side_effect(file, signal_fn, wait_s)` —— GUI 应用对文件做的副作用是它"真的执行了某件事"的可信信号。今天用于：
- vault 加载验证 = workspace.json mtime delta
- latex 渲染验证 = files[fid].dataURL size delta

要进 oracle / acceptance 体系作为通用 probe。
