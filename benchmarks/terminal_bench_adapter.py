"""terminal-bench adapter for our OpenAIAdapter / AgentLoop scaffold.

terminal-bench runs each task in a Dockerized tmux sandbox and calls
`BaseAgent.perform_task(instruction, session, logging_dir)` once per task.
We wrap `TmuxSession` in a Bash-shaped tool and let our AgentLoop drive the
session exactly like it would drive a local shell — same model plumbing,
same tool-calling loop, same prompting — only the shell backend differs.

Tool shape mirrors Terminus's `Command` (keystrokes + is_blocking + timeout_sec)
because that's the contract tmux wants: the model must decide when to block
vs. when to fire-and-forget (interactive programs, long-running processes).
After each call we return incremental terminal output so the model can see
what actually happened.

Register with terminal-bench via `--agent-import-path`:
    tb run --agent-import-path benchmarks.terminal_bench_adapter:OurScaffoldTBAgent \
           --model-name gpt-5.4 --task-id hello-world
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# --- Windows path compat for terminal-bench (container paths). -------------
# terminal-bench constructs Linux container paths with `Path("/tmp/...")`,
# which on Windows becomes a WindowsPath that stringifies to `\tmp\...`.
# Docker's Linux containers don't recognize that, so `put_archive` 404s.
# Patch the class-level container paths to PurePosixPath before any harness
# code imports. Safe on non-Windows because posix paths already stringify
# correctly there.
def _patch_tb_windows_paths() -> None:
    import sys
    if sys.platform != "win32":
        return
    from pathlib import PurePosixPath
    from terminal_bench.terminal import tmux_session as _tmux
    from terminal_bench.terminal import docker_compose_manager as _dcm

    _tmux.TmuxSession._GET_ASCIINEMA_TIMESTAMP_SCRIPT_CONTAINER_PATH = (
        PurePosixPath("/tmp/get-asciinema-timestamp.sh")
    )
    _dcm.DockerComposeManager.CONTAINER_TEST_DIR = PurePosixPath("/tests")
    # The docker_compose_manager.CONTAINER_*_PATH are plain strings ("/logs",
    # "/agent-logs") — the bug is only when tmux_session.py re-wraps them in
    # Path(). Patch the callsites by wrapping the constants in a PurePosixPath-
    # compatible sentinel: easier to replace the two properties directly.
    def _logging_path(self):
        return PurePosixPath(_dcm.DockerComposeManager.CONTAINER_SESSION_LOGS_PATH) / (
            f"{self._session_name}.log"
        )

    def _recording_path(self):
        if self._disable_recording:
            return None
        return PurePosixPath(_dcm.DockerComposeManager.CONTAINER_SESSION_LOGS_PATH) / (
            f"{self._session_name}.cast"
        )

    _tmux.TmuxSession.logging_path = property(_logging_path)
    _tmux.TmuxSession._recording_path = property(_recording_path)


_patch_tb_windows_paths()

from agent.core.loop import (
    AgentLoop,
    LoopConfig,
    LoopContext,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from agent.models.openai_adapter_v2 import OpenAIAdapter
from agent.models.openai_responses_adapter import OpenAIResponsesAdapter

from terminal_bench.agents.base_agent import AgentResult, BaseAgent
from terminal_bench.agents.failure_mode import FailureMode
from terminal_bench.terminal.tmux_session import TmuxSession

load_dotenv()


SYSTEM_PROMPT = """
You are an AI assistant solving a command-line task in a Linux environment.
You operate a tmux session inside a Docker container via the `tmux_bash` tool.

Workflow:
1. Analyze the current terminal state.
2. Call `tmux_bash` with the keystrokes you want to send.
3. Read the new terminal output returned by the tool and decide the next step.
4. When the task is complete, reply with a brief confirmation and stop calling
   tools.

Tool usage rules:
- `keystrokes`: the literal string to send. For a command to execute, append
  "\\n" at the end (e.g. "ls -la\\n"). Use tmux escape sequences for modifier
  keys (e.g. "C-c", "Escape"). Send modifier keys as their own call.
- `is_blocking=true`: wait for the command to finish before returning. Only
  use for non-interactive commands executed at a shell prompt. Never block on
  interactive programs (vim, less, git diff) or on sending modifier keys.
- `is_blocking=false`: fire-and-forget; returns after a short min-timeout.
  Use for interactive programs and background processes.
- `timeout_sec`: upper bound for blocking calls (default 30, max 600).

When in doubt, prefer is_blocking=false and poll with a zero-keystroke call
to re-read the screen.

Be terse. Do not narrate every step — just act.
""".strip()


class TmuxBashTool:
    """Bash-shaped tool that drives a live TmuxSession.

    Exactly one instance per task (one session per task). We keep the session
    reference on `self`; AgentLoop treats this as a normal tool.
    """

    name = "tmux_bash"
    description = (
        "Send keystrokes to the tmux session and return new terminal output. "
        "Use this for every interaction with the shell."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "keystrokes": {
                "type": "string",
                "description": (
                    "Keystrokes to send. Append '\\n' for commands that should "
                    "execute. Use tmux escape sequences for modifier keys."
                ),
            },
            "is_blocking": {
                "type": "boolean",
                "description": (
                    "Wait for the command to finish (only for non-interactive "
                    "shell commands). Default false."
                ),
                "default": False,
            },
            "timeout_sec": {
                "type": "number",
                "description": "Max wait for blocking calls. Default 30.",
                "default": 30,
            },
        },
        "required": ["keystrokes"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False  # a tmux session is a single serial resource

    def __init__(self, session: TmuxSession):
        self._session = session

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        keystrokes = input.get("keystrokes", "")
        is_blocking = bool(input.get("is_blocking", False))
        timeout_sec = float(input.get("timeout_sec", 30))

        if not keystrokes:
            # Zero-keystroke call = re-read the screen.
            output = self._session.get_incremental_output()
            return ToolResultBlock(tool_use_id="", content=output, is_error=False)

        # tmux ops are blocking sync calls; run in a worker thread so we don't
        # stall the event loop (AgentLoop.run is an async generator).
        def _send() -> str:
            try:
                self._session.send_keys(
                    keystrokes,
                    block=is_blocking,
                    max_timeout_sec=min(timeout_sec, 600.0),
                )
            except TimeoutError:
                return (
                    f"[TimeoutError after {timeout_sec}s]\n"
                    f"{self._session.capture_pane()}"
                )
            return self._session.get_incremental_output()

        output = await asyncio.to_thread(_send)
        return ToolResultBlock(tool_use_id="", content=output, is_error=False)


class OurScaffoldTBAgent(BaseAgent):
    """terminal-bench agent that runs our AgentLoop over a tmux session."""

    @staticmethod
    def name() -> str:
        return "our_scaffold"

    def __init__(
        self,
        model_name: str,
        max_episodes: int = 50,
        api_base: Optional[str] = None,
        temperature: Optional[float] = None,
        provider: str = "openai",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._model_name = model_name
        self._max_episodes = max_episodes
        self._api_base = api_base or os.getenv("OPENAI_BASE_URL")
        self._temperature = temperature
        self._provider = provider

    def _build_adapter(self):
        if self._provider == "qwen":
            return OpenAIResponsesAdapter(model=self._model_name, provider="qwen")
        return OpenAIAdapter(
            model=self._model_name,
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=self._api_base,
        )

    def perform_task(
        self,
        instruction: str,
        session: TmuxSession,
        logging_dir: Path | None = None,
    ) -> AgentResult:
        adapter = self._build_adapter()
        tool = TmuxBashTool(session)
        loop = AgentLoop(
            adapter=adapter,
            tools={tool.name: tool},
            config=LoopConfig(
                max_iterations=self._max_episodes,
                parallel_tool_calls=False,  # one tmux session = one actor
                system_prompt=SYSTEM_PROMPT,
            ),
        )

        initial_screen = session.capture_pane()
        user_message = (
            f"Task:\n{instruction}\n\n"
            f"Initial terminal state:\n{initial_screen}"
        )

        text_tokens_out: list[str] = []
        tool_call_count = 0

        async def _drive() -> None:
            nonlocal tool_call_count
            async for event in loop.run(user_message):
                if isinstance(event, Message) and event.role == Role.ASSISTANT:
                    for b in event.content:
                        if isinstance(b, TextBlock):
                            text_tokens_out.append(b.text)
                        elif isinstance(b, ToolUseBlock):
                            tool_call_count += 1

        try:
            asyncio.run(_drive())
            failure_mode = FailureMode.NONE
        except Exception as e:
            # Surface unexpected errors but don't crash the whole harness.
            failure_mode = FailureMode.UNKNOWN_AGENT_ERROR
            if logging_dir is not None:
                (logging_dir / "agent_error.txt").write_text(repr(e))

        # We don't have token counts from OpenAIAdapter in a structured form
        # yet; return 0 rather than guess. terminal-bench's metrics panel is
        # permissive about zeros.
        return AgentResult(
            total_input_tokens=0,
            total_output_tokens=0,
            failure_mode=failure_mode,
            timestamped_markers=[],
        )
