"""End-to-end REAL task: drive the FULL agent architecture (skill-injected,
full-access, tool-orchestrating) to annotate formula (1) on the user's actual
Obsidian Excalidraw canvas with a properly LaTeX-rendered, positioned, grouped
annotation. Validates the framework — the agent must do the rendering +
placement + grouping itself (the obsidian-excalidraw SKILL teaches the how).

Run: python -m tests.harness_bench._real_task [model]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

from tests.harness_bench.agent_runner import (
    server, _create_conversation, _parse_sse, _extract_tool_trace,
)

CANVAS = r"D:\D\科研笔记\AI\通用AI\RL\机器人控制\Drawing 2026-06-05 15.11.16.excalidraw.md"

PROMPT = f"""请在这个 Obsidian Excalidraw 画布上，为论文《Latent Action Diffusion for Cross-Embodiment Manipulation》的【公式 (1)】添加一段**带 LaTeX 渲染**的注释 / 推导。

画布文件：{CANVAS}

背景事实：
- 画布把该论文 8 页 PDF 作为 8 个 image 元素垂直堆叠；markdown 的 `## Embedded Files` 段里有 fileId → PDF 页 的映射。
- 公式 (1) 在 PDF 第 3 页，对应画布上的 image 元素 id="V8E4b9yg"（x=174, y=-1825, 宽 734, 高 950）。该公式在该页左栏、约 43% 高度，换算到画布 y 大约 -1410。
- 公式 (1) 原文： x_i = ( x_i^H , f_H^{{R_1}}(x_i^H) , … , f_H^{{R_M}}(x_i^H) )，其中 f_H^{{R_j}}（j=1..M）是从人手到第 j 个机器人本体的 retargeting（重定向）函数，x_i^H 是第 i 个人手位姿。

要求：
1. 用 **LaTeX 渲染**公式 (1)（按 skill 里 Obsidian 的正确做法做；写入工具支持的 LaTeX 渲染方式照 skill 走，确保用户能在画板上看到渲染后的公式而不是占位框）。
2. 在公式图下方加一段**中文说明**，讲清楚公式含义、各符号、以及它在方法里的作用（它构造的是"跨本体对齐"的末端位姿元组，作为 Stage-2 对比学习的监督信号；同元组正样本、异元组负样本，学到跨本体共享隐动作空间）。说明文字用干净写法，不要出现裸的 `^{{}}` `_`。
3. 整个注释放在公式 (1) 附近（建议页面左侧空白处、与公式齐平），并加一条**箭头**从注释指向公式 (1)。
4. 把公式图、说明文字、箭头**编成一组**（共享同一个 groupId），让它们能一起移动。
5. **不要修改或删除**任何已有的 16 个元素（8 个矩形 + 8 个 PDF 页图）。
完成后读回画布确认新元素已加入、原有元素未动。
"""


def main() -> int:
    # Coding Plan 端点 (/api/coding/v3) 覆盖模型；deepseek-v4-pro 纯文本，
    # LaTeX 渲染由 obsidian 写入工具层兜底，无需多模态。
    model = sys.argv[1] if len(sys.argv) > 1 else "deepseek-v4-pro"
    out_dir = Path("tests/harness_bench/bench_results/_real")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"== END-TO-END REAL task | model={model}")
    with server(out_dir) as base_url:
        conv = _create_conversation(base_url, "annotate-eq1-e2e", profile="doubao-code")
        payload = {
            "conversation_id": conv, "message": PROMPT, "history": [],
            "mode": "full-access", "max_iterations": 40, "model": model,
        }
        started = time.time()
        raw = []
        try:
            with requests.post(f"{base_url}/api/agent_chat_v2", json=payload,
                               stream=True, timeout=1500) as r:
                r.raise_for_status(); r.encoding = "utf-8"
                for chunk in r.iter_content(chunk_size=None, decode_unicode=False):
                    if chunk:
                        raw.append(chunk.decode("utf-8", errors="replace"))
        except Exception as exc:
            print(f"!! request error after {time.time()-started:.0f}s: {type(exc).__name__}: {exc}")
        elapsed = time.time() - started
        blob = "".join(raw)
        (out_dir / "raw_sse_e2e.txt").write_text(blob, encoding="utf-8")
        events = _parse_sse(blob)
        acts = [e["data"] for e in events if e["event"] == "activity"]
        names, trace = _extract_tool_trace(acts)
        done = next((e["data"] for e in events if e["event"] == "done"), {})
        print(f"== elapsed {elapsed:.0f}s | {len(names)} tool calls")
        for t in trace:
            inp = t.get("input") or {}
            short = inp.get("command") or inp.get("canvas_path") or inp.get("path") or inp.get("old_string") or ""
            print(f"   - {t['name']:>34} err={t['is_error']} | {str(short)[:46]}")
            if t['is_error']:
                print("       ERR:", str(t.get('detail') or '')[:130])
        print(f"== done: {done}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
