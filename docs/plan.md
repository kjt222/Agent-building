# Agent 重构计划（模仿 Claude Code 主体，多 provider 可切换）

**创建日期：** 2026-04-18
**目标：** 把现有半成品重写为 Claude Code 风格的 agent 主体：single-loop + tool-use + streaming + permission + sub-agent + skill + MCP。保持模型可切换（默认 GPT，能换 Claude/Gemini/DeepSeek），在公开基准上验证效果不劣化。

---

## 核心原则（Anthropic 哲学）

1. **少而强的工具**。工具数量本身是成本——每个 schema 占 context，数量越多模型越容易误选（context poisoning）。优先合并同类；让模型用 `Bash`+脚本完成小众需求，而不是为每种小需求新增工具。
2. **单循环 agentic loop**。模型每轮自主决定下一步，不做先规划后执行的两阶段。退役 `planner.py`。
3. **工具返回格式统一**。主循环只认一种内部格式；provider adapter 负责和各家 API 的 tool-use 协议双向翻译。
4. **Sub-agent 是一个特殊的 tool**，不是另一套系统。Skill 是关键词触发的 prompt+tool bundle，不是 tool。扩展新应用优先走 MCP，不往 `tools/` 塞代码。
5. **默认最少上下文**。Skill、MCP 工具按需加载；permission gate 拦住危险操作。

---

## 模型无关架构（关键决定）

主循环只认内部标准化消息格式，定义一套：

```
InternalMessage = {role, content: list[Block]}
Block = TextBlock | ToolUseBlock | ToolResultBlock
ToolUseBlock = {id, name, input: dict}
ToolResultBlock = {tool_use_id, content, is_error}
```

各 provider adapter 实现：
- `to_provider(messages, tools) -> provider_api_payload`
- `from_provider(response) -> InternalMessage`
- `stream(messages, tools) -> AsyncIterator[Delta]`

这样切换 GPT-4o / Claude / Gemini / DeepSeek 只改配置，不动主循环。

---

## Phase 0 — 工具裁剪与目录落地

**动作：**
- 清点 `tools/` 下所有注册工具，按"调用频次 × 不可替代性"打分
- 合并同类：`docx_editor` + `xlsx_editor` → 评估是否干脆让模型用 `Bash`+python-docx/openpyxl 脚本处理
- 删除 rarely-used（<3 次调用的）
- 子目录 `tools/{filesystem,system,knowledge,memory}/` 要么填满，要么拍平
- **初版目标工具集（无硬上限，但越少越好）：**
  - 核心：`Read` / `Write` / `Edit` / `Grep` / `Glob` / `Bash`
  - 知识：`KnowledgeSearch`（包 RAG）
  - 记忆：`Memory`（读写持久 memory）
  - 扩展：`TaskCreate`（sub-agent）/ `UserAsk`（反问用户）/ `Notify`
  - 专用：desktop/office 按需（或移到 skill）

**验收：** `registry.py` 每个工具 description ≤4 行；`tools/` 下无空壳子目录。

---

## Phase 1 — 主循环重写

**动作：**
- 新建 `agent/core/loop.py`：`while not done: call_model(stream) → dispatch_tool_uses → append_tool_results`
- 支持**并行 tool calls**：一个 assistant 消息里多个 `ToolUseBlock` 同时 dispatch（asyncio.gather）
- **Streaming**：adapter 返回 async iterator，UI 层消费 token 增量
- 删除 `planner.py`
- `core/executor.py` 收编为 loop 内部的 dispatcher（或合并进 loop.py）

**验收：** 并行 3+ 个 `Read` 的任务能真并行；UI 端 token 流式出现。

---

## Phase 2 — Tool 协议规范化

**动作：**
- 统一 `BaseTool`：`name / description / input_schema(JSON Schema) / run(input) / permission_level / parallel_safe`
- 统一返回：`{content: str | list[Block], is_error: bool}`
- 每个工具声明权限等级：`safe / needs_approval / dangerous`
- 每个工具声明 `parallel_safe: bool`
- 写 `validate_tool(tool)` 静态检查器

**验收：** 所有工具通过静态检查；主循环根据 `parallel_safe` 决定是否并行。

---

## Phase 3 — Model Adapter 归一化（多 provider 关键）

**动作：**
- 定义 `agent/models/base.py` 的统一接口（见上方"模型无关架构"）
- 实现/修 adapter：`openai_adapter`（默认）、`anthropic_adapter`、`gemini_adapter`、`deepseek_adapter`、`zhipu_adapter`
- 每个 adapter 必须支持：streaming、tool_use 双向翻译、错误重试、token 计数
- 写 adapter 一致性测试：同一组 `InternalMessage + tools`，各 adapter 都能跑通一个最小 tool-use 回合
- 配置切换：`config/models.yaml` 声明 active provider + model id

**验收：** 同一 agent 任务在 4 个 provider 上都能跑完；测试套件覆盖至少一个 tool-use 回合。

---

## Phase 4 — Permission / Hook 系统

**动作：**
- `core/permissions.py`：modes = `plan / ask / accept-edits / bypass`
- **PreToolUse hook**：可拦截、改写 args、拒绝，返回 `allow/deny/ask`
- **PostToolUse hook**：可加工返回、写审计日志
- **Stop hook**：turn 结束时触发（写 conversation.md、持久 memory、压缩）
- **Intent-without-action Stop hook**（已实现，见 `agent/core/hooks.py`）：当 assistant `stop_reason=end_turn` 且最后一轮只有 text、没有 tool_use，但 text 含明显意图短语（EN/ZH regex）时，自动追加 user 催促并 resume 循环；`max_nudges` 上限防死循环。
- hooks 配置走 `config/hooks.yaml`，不硬编码

**验收：** `Bash` 首次运行弹确认；关闭确认后能继续；intent-hook 的 smoke test 不再误停。

---

## Phase 5 — Sub-agent（作为一个 Tool）

**动作：**
- `tools/task.py`：输入 `{description, prompt, agent_type?, tool_subset?}`
- 子 agent 复用主 loop：**独立 context**、可限制 tool 子集、独立 system prompt
- 子 agent 只回**最终摘要**给主 agent，不回原始工具调用历史
- 支持 `run_in_background` + 结果轮询（后续）
- 定义 agent 角色：`agents/*.yaml`（name / description / tools / system_prompt / model）

**验收：** 主 agent 调用 "Explore" 子 agent 搜一批文件，主上下文只收到摘要。

---

## Phase 6 — Skills 系统

**动作：**
- `skills/<name>/SKILL.md` + frontmatter（trigger 关键词 / 描述 / 额外启用的 tools）
- Skill loader：仅当匹配触发词时才注入 system prompt 片段
- 迁移：`office` / `rag-qa` / `docx/xlsx 编辑` 大概率应该从 tool 变成 skill

**验收：** 默认对话 system prompt 不含 office 相关描述；用户说"改 xlsx"时才加载对应 skill。

---

## Phase 7 — MCP 接入

**动作：**
- `agent/mcp/` 实现 MCP client（stdio + http）
- `config/mcp_servers.yaml` 声明外部 server
- MCP tools 和内置 tools 在主循环里统一调度
- MCP tools 默认 `needs_approval`

**验收：** 接一个本地 MCP server（如 filesystem-mcp）并能在对话中调用。

---

## Phase 8 — Compactor / Memory 瘦身

**动作：**
- 现有 `compactor.py` 576 行 → 目标 ≤250 行：只保留 "keep recent N turns + summarize older"
- 和 Stop hook 联动，每 turn 结束写 `MEMORY.md` / `conversation.md`
- 不依赖任何 provider 的 managed compaction（要保持可移植）

**验收：** 长对话不爆 context；代码行数达标。

---

## Phase 9 — 基准评测

**目标：** 验证重构后能力不劣化，且多 provider 行为可比。

**动作：**
- 引入 **τ-bench**（Anthropic/Sierra 发布，专评 tool-use agent）作为主基准
  - GitHub: sierra-research/tau-bench
  - 两个子场景：airline / retail，都是多轮 tool 调用
- 辅助：SWE-bench Verified 子集（如果 agent 会碰代码任务）
- 评测流水线：`tests/bench/`
  - 跑 provider × benchmark 矩阵
  - 记录 pass@1、平均 turn 数、平均 token 消耗
- 在重构各 phase 前后各跑一次，留基线

**验收：** 重构后 pass@1 ≥ 基线；至少 GPT 和另一个 provider 在 τ-bench 上跑通。

---

## 执行顺序

```
必须按顺序：  0 → 1 → 2 → 3
可并行或按需：4 / 5 / 6 / 7 / 8
全程持续：    9（每个 phase 前后打点）
```

**不要先做：** sub-agent / skill / MCP（都依赖 tool 协议 + 主循环就绪）。

---

## 待决议事项

- 工具最终目标数？（倾向 12-15，无硬上限但每新增需给理由）
- `office`、`desktop` 这些业务逻辑是转 skill 还是保留为 tool？
- τ-bench 的 airline/retail 场景是否够贴你后续的"某些应用"？需要可扩展评测集？
