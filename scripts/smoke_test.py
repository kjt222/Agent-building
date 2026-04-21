"""End-to-end smoke test for the new AgentLoop + OpenAI adapter.

Loads OPENAI_API_KEY from .env (simple parse; no python-dotenv dependency).
Runs a small task that exercises Glob, Read, and Bash.

Usage:
    .venv/Scripts/python.exe scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Windows console: force UTF-8 so Chinese / emoji don't garble.
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
    Hooks,
    LoopConfig,
    Message,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agent.core.hooks import make_intent_without_action_hook  # noqa: E402
from agent.models.openai_adapter_v2 import OpenAIAdapter  # noqa: E402
from agent.tools_v2.primitives import full_toolset  # noqa: E402


SYSTEM = """You are a coding agent running in a local repo. Use tools to answer.
When asked to do something, call a tool now rather than announcing intent.
Do not describe what you will do; do it. Be concise.
"""


async def main(prompt: str):
    adapter = OpenAIAdapter(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    tools = full_toolset()
    hooks = Hooks(on_stop=[make_intent_without_action_hook()])
    loop = AgentLoop(
        adapter=adapter,
        tools=tools,
        hooks=hooks,
        config=LoopConfig(
            max_iterations=15,
            parallel_tool_calls=True,
            system_prompt=SYSTEM,
        ),
    )

    print(f"\n[USER] {prompt}\n")
    async for event in loop.run(prompt):
        if isinstance(event, Message):
            if event.role == Role.ASSISTANT:
                for b in event.content:
                    if isinstance(b, TextBlock) and b.text:
                        print(f"[ASSISTANT] {b.text}")
                    elif isinstance(b, ToolUseBlock):
                        print(f"[TOOL CALL] {b.name}({b.input})")
            else:  # USER with tool results
                for b in event.content:
                    if isinstance(b, ToolResultBlock):
                        preview = b.content if isinstance(b.content, str) else str(b.content)
                        preview = preview[:300] + ("..." if len(preview) > 300 else "")
                        tag = "[TOOL ERR]" if b.is_error else "[TOOL OK]"
                        print(f"{tag} {preview}")


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or (
        "Find all Python files under agent/core/, then tell me which file is the newest "
        "and give a one-sentence summary of it."
    )
    asyncio.run(main(task))
