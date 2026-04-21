"""Minimal live smokes for AgentLoop against OpenAI gpt-5.4-mini.

Two modes:
  basic  — Read tool round-trip (SAFE). Confirms tool-call -> tool-result -> final text.
  plan   — Plan mode gating. Write is blocked until exit_plan_mode is called.

Usage:
    .venv/Scripts/python.exe scripts/smoke_live_loop.py basic
    .venv/Scripts/python.exe scripts/smoke_live_loop.py plan

Kept deliberately small: max_iterations=6, tiny fixture file, prints final usage.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env()

from agent.core.loop import (  # noqa: E402
    AgentLoop,
    LoopConfig,
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agent.models.openai_adapter_v2 import OpenAIAdapter  # noqa: E402
from agent.tools_v2.control import ExitPlanModeTool  # noqa: E402
from agent.tools_v2.primitives import ReadTool, WriteTool  # noqa: E402


MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")

SYSTEM_BASIC = (
    "You are a terse coding agent. When asked about a file, call Read — do not "
    "announce intent. Answer in one sentence after reading."
)

SYSTEM_PLAN = (
    "You are in PLAN MODE. Writes/edits are blocked until you call exit_plan_mode "
    "with a one-paragraph plan. If a write is blocked, immediately call "
    "exit_plan_mode, then retry the write. Be terse."
)


async def _drive(loop: AgentLoop, prompt: str) -> None:
    print(f"\n[USER] {prompt}\n")
    async for event in loop.run(prompt):
        if isinstance(event, Message):
            if event.role == Role.ASSISTANT:
                for b in event.content:
                    if isinstance(b, TextBlock) and b.text:
                        print(f"[ASSISTANT] {b.text}")
                    elif isinstance(b, ToolUseBlock):
                        print(f"[TOOL CALL] {b.name}({b.input})")
            else:
                for b in event.content:
                    if isinstance(b, ToolResultBlock):
                        preview = b.content if isinstance(b.content, str) else str(b.content)
                        preview = preview[:200] + ("..." if len(preview) > 200 else "")
                        tag = "[TOOL ERR]" if b.is_error else "[TOOL OK]"
                        print(f"{tag} {preview}")


def _print_usage(trace_path: Path):
    if not trace_path.exists():
        return
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    total = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0}
    for line in lines:
        rec = json.loads(line)
        u = rec.get("usage") or {}
        for k in total:
            total[k] += u.get(k, 0) or 0
    print(f"\n[USAGE] {total}  (turns={len(lines)})")


async def smoke_basic():
    fixture = ROOT / "tmp" / "live_smoke_basic.txt"
    fixture.parent.mkdir(exist_ok=True)
    fixture.write_text("The magic number is 42.\n", encoding="utf-8")
    trace = ROOT / "tmp" / "live_smoke_basic.trace.jsonl"
    if trace.exists():
        trace.unlink()

    adapter = OpenAIAdapter(model=MODEL)
    loop = AgentLoop(
        adapter=adapter,
        tools={"Read": ReadTool()},
        config=LoopConfig(
            max_iterations=4,
            parallel_tool_calls=True,
            system_prompt=SYSTEM_BASIC,
            trace_path=trace,
        ),
    )
    await _drive(
        loop,
        f"What is the magic number? Read {fixture.as_posix()} to find out.",
    )
    _print_usage(trace)


async def smoke_plan():
    fixture = ROOT / "tmp" / "live_smoke_plan.txt"
    fixture.parent.mkdir(exist_ok=True)
    # ensure starting state
    if fixture.exists():
        fixture.unlink()

    trace = ROOT / "tmp" / "live_smoke_plan.trace.jsonl"
    if trace.exists():
        trace.unlink()

    adapter = OpenAIAdapter(model=MODEL)
    loop = AgentLoop(
        adapter=adapter,
        tools={
            "Write": WriteTool(),
            "exit_plan_mode": ExitPlanModeTool(),
        },
        config=LoopConfig(
            max_iterations=6,
            parallel_tool_calls=False,
            system_prompt=SYSTEM_PLAN,
            permission_mode="plan",
            trace_path=trace,
        ),
    )
    await _drive(
        loop,
        f"Write the single word 'hello' to {fixture.as_posix()} (overwrite if exists).",
    )
    _print_usage(trace)
    print(f"[FINAL FILE EXISTS] {fixture.exists()}")
    if fixture.exists():
        print(f"[FINAL CONTENT] {fixture.read_text(encoding='utf-8')!r}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "basic"
    if mode == "basic":
        asyncio.run(smoke_basic())
    elif mode == "plan":
        asyncio.run(smoke_plan())
    else:
        print(f"unknown mode: {mode} (expected: basic | plan)")
        sys.exit(2)


if __name__ == "__main__":
    main()
