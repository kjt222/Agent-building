# harness_bench — 固定模型 + 锁定测试集，迭代 harness

参考 LangChain DeepAgents CLI 的 Terminal Bench 2.0 harness-engineering 案例。
模型固定（baseline: `deepseek-v4`），每轮 harness 改动（system prompt / tools /
middleware / verifier）后跑全集，pass 率曲线写进 `docs/experiments.md`。

## 跑

```powershell
.venv/Scripts/python.exe -m tests.harness_bench.run_bench --profile deepseek-v4
.venv/Scripts/python.exe -m tests.harness_bench.run_bench --profile deepseek-v4 --tasks 13,14
.venv/Scripts/python.exe -m tests.harness_bench.run_bench --profile deepseek-v4 --base-url http://127.0.0.1:8000
```

输出：`tests/harness_bench/bench_results/<ts>_<profile>/`
- `summary.json`：总分 + by-tier 通过率 + 每题 result
- `task_NN/`：每题独立目录，含 `raw_sse.txt`、`events.jsonl`、`result.json`、（fail 时）`task_error.txt`

## 题集

14 题，4 个 tier。详细列表见 `docs/conversation.md` 的 P18 章节。

| Tier | 范围 | 题数 |
|---|---|---|
| P18-A | Obsidian doc read/write | 3 |
| P18-B | Excalidraw canvas mutation | 5 |
| P18-C | Desktop observe/act/verify | 4 |
| P18-D | Verifier 红灯体检（假阳性必须报红） | 2 |

> Tier 名带 `P18-` 前缀是为了和 `agent.eval` 里的 `tier_a`（Word/Excel/PPTX office 套件）
> 区分开 —— 那是另一套独立测试集，两边都叫 "Tier A" 容易混。
> `summary.json` 的 `by_tier_pass / by_tier_fail` 里也是 `P18-A/B/C/D` 这套 key。

**P18-D 最重要**：如果 D 题让 agent "蒙混过关" pass，意味着 verifier 红灯失效，
所有 A/B/C 分数都不可信。先写、先盯。

## 加新题

每个 `task_NN_<slug>.py` 必须实现：

```python
PROMPT: str                # 给 agent 的指令
MODE: str = "read-only"    # "read-only" | "full-access"
PROFILE_OVERRIDE: str = "" # 强制 profile（默认用 --profile）
TIMEOUT_S: float = 240.0

def setup() -> dict:
    """Prepare fixtures. Return state dict accessible from verify().

    Can set state["_prompt"] to override PROMPT after setup (e.g. inject
    a fresh temp file path into the prompt).
    """
    return {}

def verify(outcome, state) -> tuple[bool, str]:
    """outcome: RunOutcome from agent_runner.run_prompt.
    Return (passed, human_readable_reason).
    """
    ...

def teardown(state) -> None:
    """Optional cleanup."""
    ...
```

`outcome` 字段（`base.RunOutcome`）：
- `tool_calls: list[str]` — 调用过的 tool 名（按时序）
- `tool_trace: list[dict]` — 每次 tool_result 的 `{id, name, input, is_error, detail, parsed}`
- `assistant_text: str` — 模型最后的文本输出
- `manifest_tools: list[str]` — 这个 mode 下注册的 tool 列表
- `capability_scope`、`done`、`elapsed_s`、`error`

辅助：`base.tool_was_called(outcome, "X")` / `base.tool_results_for(outcome, "X")`

## refs/

`_tmp_fix_latex_link.py` 和 `_tmp_remove_frame.py` 是 Tier B 第 6 / 7 题的参考实现，
写题目时对照它们的终态做 verifier。
