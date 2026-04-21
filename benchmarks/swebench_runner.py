"""SWE-bench Verified runner for our AgentLoop.

Per instance:
  1. Clone the repo @ base_commit into a scratch workdir (bare-mirror cache
     per repo makes this fast after the first clone).
  2. Stand up our AgentLoop with Read/Write/Edit/Grep/Glob/Bash primitives
     scoped to that workdir.
  3. Feed the agent the `problem_statement` as its user message.
  4. Run until stop; `git diff` against base_commit to extract the model patch.
  5. Append `{instance_id, model_name_or_path, model_patch}` to a predictions
     JSONL, plus a run-level log to results/swebench_<slice>.log.

This script produces *predictions only*; evaluation against FAIL_TO_PASS /
PASS_TO_PASS requires the official SWE-bench harness, which is Linux-only.
Run it from WSL after this script finishes:

    cd /mnt/d/... && python -m swebench.harness.run_evaluation \\
      --predictions_path predictions.jsonl \\
      --dataset_name princeton-nlp/SWE-bench_Verified \\
      --run_id our_scaffold_v1

Usage:
    python -m benchmarks.swebench_runner --num-instances 3 --slice-id smoke
    python -m benchmarks.swebench_runner --num-instances 50 --concurrency 4 \\
        --slice-id v1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import uuid

import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from agent.core.loop import (
    AgentLoop,
    LoopConfig,
    Message,
    PermissionLevel,
    Role,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    TurnEnd,
)
from agent.models.openai_adapter_v2 import OpenAIAdapter
from agent.models.openai_responses_adapter import OpenAIResponsesAdapter
from agent.tools_v2.primitives import BashTool, EditTool, GlobTool, GrepTool, ReadTool, WriteTool


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_PARQUET = (
    REPO_ROOT / "datasets" / "swebench_verified" / "hf" / "data" / "test-00000-of-00001.parquet"
)
RESULTS_DIR = REPO_ROOT / "datasets" / "swebench_verified" / "results"
REPO_CACHE = REPO_ROOT / "datasets" / "swebench_verified" / "repo_cache"
WORKDIR_ROOT = REPO_ROOT / "datasets" / "swebench_verified" / "workdirs"

MODEL_NAME_OR_PATH = "our_scaffold_gpt-5.4"

SYSTEM_PROMPT_LOCAL = """\
You are a software engineering agent solving a bug fix or feature task on a real open-source Python repository.

You operate in a local checkout of the repository. Use the Read / Glob / Grep tools to investigate. Use Edit (for small, targeted changes) or Write (for new files) to modify code. Use Bash sparingly — do NOT try to install dependencies or run the test suite; the CI harness handles evaluation.

Your goal is to produce a minimal, correct patch that resolves the issue described by the user. When you believe the fix is complete, stop calling tools and send a brief final message describing the change.

Guidelines:
- Always Read a file before Editing it.
- Keep the change surgical — do not refactor unrelated code.
- Preserve the existing code style (indentation, quoting, naming).
- Do not add comments explaining your fix unless they clarify genuinely non-obvious behavior.
- Do not edit test files; your patch is evaluated against pre-existing tests."""

SYSTEM_PROMPT_DOCKER = """\
You are a software engineering agent solving a bug fix or feature task on a real open-source Python repository.

You have:
- Local file tools (Read / Write / Edit / Glob / Grep) that operate on the repository checkout.
- A `Bash` tool that runs inside a Linux Docker container with the same repository mounted at /repo (working directory). The container has Python 3.11, git, and gcc available. You can install dependencies and run tests here.

Workflow:
1. Investigate the issue using Read / Glob / Grep.
2. Form a hypothesis and make a minimal, surgical edit with Edit (or Write for a new file).
3. VERIFY your fix: use Bash to install the project (e.g. `pip install -e . --quiet`) and run the specific failing tests named in the issue (or the relevant test module). If the test suite passes, you are done.
4. If tests fail, iterate: read the test output, refine the fix, and re-run.
5. Stop calling tools when the targeted tests pass. Your final patch is auto-extracted from the checkout.

Guidelines:
- Always Read a file before Editing it.
- Keep the change surgical — do not refactor unrelated code.
- Preserve the existing code style (indentation, quoting, naming).
- Do not edit test files; your patch is evaluated against pre-existing tests.
- Prefer targeted pytest invocations (`pytest path/to/test_x.py::TestCls::test_fn -x -v`) over full-suite runs — they're much faster and focus your debugging.
- If `pip install -e .` fails, try installing only the minimal deps needed for the affected module, or run tests with PYTHONPATH=. if the package is pure-python.
- Bash timeout defaults to 120s; for longer installs use `timeout=600`."""


# --------------------------------------------------------------------------- #
# Repo sandbox management
# --------------------------------------------------------------------------- #


def _repo_cache_path(repo: str) -> Path:
    """~/.../repo_cache/astropy__astropy.git — bare mirror per repo."""
    return REPO_CACHE / f"{repo.replace('/', '__')}.git"


def _git(args: list[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (cwd={cwd}): {proc.stderr.strip()}"
        )
    return proc


def ensure_repo_cache(repo: str) -> Path:
    """Clone a bare mirror once per repo; reuse across instances."""
    cache = _repo_cache_path(repo)
    if cache.exists():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"
    print(f"[cache] cloning bare mirror: {url}", flush=True)
    _git(["clone", "--mirror", url, str(cache)])
    return cache


def prepare_workdir(repo: str, base_commit: str, instance_id: str) -> Path:
    """Create a fresh working checkout at base_commit."""
    cache = ensure_repo_cache(repo)
    workdir = WORKDIR_ROOT / instance_id
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.parent.mkdir(parents=True, exist_ok=True)
    # Fast local clone — shares objects with the bare mirror via --shared.
    _git(["clone", "--shared", str(cache), str(workdir)])
    _git(["checkout", "--detach", base_commit], cwd=workdir)
    return workdir


def extract_diff(workdir: Path) -> str:
    """Produce a patch of the agent's changes relative to base_commit."""
    # Stage any new files so git diff picks them up; exclude .gitignored junk.
    _git(["add", "-A"], cwd=workdir)
    proc = _git(["diff", "--cached", "--no-color"], cwd=workdir)
    return proc.stdout


# --------------------------------------------------------------------------- #
# Tool scoping — BashTool subclass that runs commands inside the workdir
# --------------------------------------------------------------------------- #


class ScopedBashTool(BashTool):
    """BashTool variant that runs every command with a fixed cwd."""

    def __init__(self, cwd: Path):
        self._cwd = str(cwd)

    async def run(self, input: dict, ctx):  # type: ignore[override]
        cmd = input.get("command", "")
        timeout = float(input.get("timeout", 60))
        if not cmd:
            return self._err("empty command")
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            return self._err(f"timeout after {timeout}s")
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        body = out
        if err:
            body += f"\n[stderr]\n{err}"
        body += f"\n[exit={proc.returncode}]"
        return self._ok(body) if proc.returncode == 0 else self._err(body)


# --------------------------------------------------------------------------- #
# Docker sandbox + container-backed Bash tool (Codex-style self-test path)
# --------------------------------------------------------------------------- #


class DockerSandbox:
    """One long-lived Linux container per instance. Workdir bind-mounted at /repo.

    Agent edits files via local Edit/Write (Windows FS) and the container sees
    them live through the bind mount. Running `pip install -e .` inside the
    container lets the agent verify fixes with `pytest` before emitting the
    final patch.
    """

    def __init__(self, workdir: Path, image: str, instance_id: str):
        self.workdir = workdir
        self.image = image
        self.name = f"swebench-{instance_id[:40].replace('/', '_')}-{uuid.uuid4().hex[:6]}"
        self._started = False

    def start(self) -> None:
        # Use docker CLI (rather than the docker SDK) — simpler subprocess
        # with predictable timeouts, and matches how tests/CI tools invoke it.
        vol = f"{self.workdir}:/repo"
        proc = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self.name,
                "-w", "/repo",
                "-v", vol,
                self.image,
                "tail", "-f", "/dev/null",
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(f"docker run failed: {proc.stderr.strip()}")
        self._started = True

    def exec(self, cmd: str, timeout: int) -> tuple[int, str, str]:
        """Run a bash command inside the container; returns (exit, stdout, stderr)."""
        if not self._started:
            raise RuntimeError("sandbox not started")
        proc = subprocess.run(
            ["docker", "exec", "-w", "/repo", self.name, "bash", "-c", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def close(self) -> None:
        if not self._started:
            return
        # `docker rm -f` handles running containers; quiet on failure since
        # this runs in a `finally` block.
        subprocess.run(
            ["docker", "rm", "-f", self.name],
            capture_output=True, text=True,
        )
        self._started = False


class DockerBashTool:
    """Bash tool that delegates to a DockerSandbox (runs in container)."""

    name = "Bash"
    description = (
        "Execute a shell command inside a Linux container with Python 3.11, git, "
        "and gcc available. Working directory is /repo (the repository root). "
        "Use this to install dependencies (`pip install -e .`) and run tests "
        "(`pytest path/to/test.py::test_name -x -v`) to verify your fix. "
        "The repository files are shared with your local Edit/Write tools via a "
        "bind mount — edits are visible in the container immediately."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to run"},
            "timeout": {
                "type": "number",
                "description": "Seconds (default 120, max 600)",
                "default": 120,
            },
        },
        "required": ["command"],
    }
    permission_level = PermissionLevel.NEEDS_APPROVAL
    parallel_safe = False

    def __init__(self, sandbox: DockerSandbox):
        self._sandbox = sandbox

    async def run(self, input: dict, ctx) -> ToolResultBlock:
        cmd = input.get("command", "")
        timeout = min(int(input.get("timeout", 120)), 600)
        if not cmd:
            return ToolResultBlock(tool_use_id="", content="empty command", is_error=True)
        try:
            code, out, err = await asyncio.to_thread(self._sandbox.exec, cmd, timeout)
        except subprocess.TimeoutExpired:
            return ToolResultBlock(
                tool_use_id="",
                content=f"[timeout after {timeout}s]",
                is_error=True,
            )
        body = out
        if err:
            body += f"\n[stderr]\n{err}"
        body += f"\n[exit={code}]"
        return ToolResultBlock(tool_use_id="", content=body, is_error=(code != 0))


def build_toolset(workdir: Path, sandbox: Optional["DockerSandbox"] = None) -> dict:
    bash_tool = DockerBashTool(sandbox) if sandbox is not None else ScopedBashTool(cwd=workdir)
    tools = [
        bash_tool,
        ReadTool(),
        WriteTool(),
        EditTool(),
        GlobTool(),
        GrepTool(),
    ]
    return {t.name: t for t in tools}


# --------------------------------------------------------------------------- #
# Agent execution per instance
# --------------------------------------------------------------------------- #


@dataclass
class InstanceResult:
    instance_id: str
    model_patch: str
    turns: int
    tool_calls: int
    elapsed_s: float
    error: Optional[str] = None


async def run_instance(
    instance: dict,
    *,
    model: str,
    max_iterations: int,
    adapter,
    docker_image: Optional[str] = None,
) -> InstanceResult:
    instance_id = instance["instance_id"]
    repo = instance["repo"]
    base_commit = instance["base_commit"]
    problem = instance["problem_statement"]
    fail_to_pass = instance.get("FAIL_TO_PASS") or instance.get("fail_to_pass") or []
    pass_to_pass = instance.get("PASS_TO_PASS") or instance.get("pass_to_pass") or []
    if isinstance(fail_to_pass, str):
        try:
            fail_to_pass = json.loads(fail_to_pass)
        except Exception:
            fail_to_pass = [fail_to_pass]
    if isinstance(pass_to_pass, str):
        try:
            pass_to_pass = json.loads(pass_to_pass)
        except Exception:
            pass_to_pass = [pass_to_pass]

    t0 = time.time()
    try:
        workdir = prepare_workdir(repo, base_commit, instance_id)
    except Exception as exc:
        return InstanceResult(
            instance_id=instance_id,
            model_patch="",
            turns=0,
            tool_calls=0,
            elapsed_s=time.time() - t0,
            error=f"workdir_prep_failed: {exc}",
        )

    sandbox: Optional[DockerSandbox] = None
    if docker_image:
        sandbox = DockerSandbox(workdir=workdir, image=docker_image, instance_id=instance_id)
        try:
            sandbox.start()
        except Exception as exc:
            return InstanceResult(
                instance_id=instance_id,
                model_patch="",
                turns=0,
                tool_calls=0,
                elapsed_s=time.time() - t0,
                error=f"docker_start_failed: {exc}",
            )

    failing_tests_hint = ""
    if fail_to_pass:
        shown = list(fail_to_pass)[:8]
        failing_tests_hint = (
            "\n\nFailing tests to target (these currently fail and must pass after your fix):\n"
            + "\n".join(f"  - {t}" for t in shown)
        )
        if len(fail_to_pass) > 8:
            failing_tests_hint += f"\n  ... and {len(fail_to_pass) - 8} more"

    user_msg = (
        f"Repository: {repo} (checked out at {base_commit[:12]}, working directory is the repo root).\n\n"
        f"Issue:\n\n{problem}"
        f"{failing_tests_hint}\n\n"
        f"Please investigate, then produce a minimal patch that resolves the issue."
    )

    tools = build_toolset(workdir, sandbox=sandbox)
    active_prompt = SYSTEM_PROMPT_DOCKER if sandbox is not None else SYSTEM_PROMPT_LOCAL

    prior_cwd = os.getcwd()
    os.chdir(str(workdir))
    try:
        loop = AgentLoop(
            adapter=adapter,
            tools=tools,
            config=LoopConfig(
                max_iterations=max_iterations,
                parallel_tool_calls=True,
                system_prompt=active_prompt,
            ),
        )

        turns = 0
        tool_calls = 0
        async for event in loop.run(user_message=user_msg):
            if isinstance(event, Message) and event.role == Role.ASSISTANT:
                turns += 1
                tool_calls += sum(
                    1 for b in event.content if isinstance(b, ToolUseBlock)
                )
    except Exception as exc:
        return InstanceResult(
            instance_id=instance_id,
            model_patch="",
            turns=0,
            tool_calls=0,
            elapsed_s=time.time() - t0,
            error=f"agent_run_failed: {exc}\n{traceback.format_exc()}",
        )
    finally:
        os.chdir(prior_cwd)
        if sandbox is not None:
            sandbox.close()

    try:
        diff = extract_diff(workdir)
    except Exception as exc:
        diff = ""
        err_suffix = f"diff_failed: {exc}"
    else:
        err_suffix = None

    return InstanceResult(
        instance_id=instance_id,
        model_patch=diff,
        turns=turns,
        tool_calls=tool_calls,
        elapsed_s=time.time() - t0,
        error=err_suffix,
    )


# --------------------------------------------------------------------------- #
# Batch driver
# --------------------------------------------------------------------------- #


def load_instances(num_instances: int, instance_ids: Optional[list[str]]) -> list[dict]:
    df = pd.read_parquet(DATASET_PARQUET)
    if instance_ids:
        df = df[df["instance_id"].isin(instance_ids)]
    else:
        df = df.head(num_instances)
    return df.to_dict(orient="records")


async def _main_async(args: argparse.Namespace) -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    WORKDIR_ROOT.mkdir(parents=True, exist_ok=True)
    REPO_CACHE.mkdir(parents=True, exist_ok=True)

    if args.concurrency != 1:
        print("[swebench] WARNING: concurrency > 1 is unsafe (os.chdir race). Forcing to 1.", flush=True)
        args.concurrency = 1

    instances = load_instances(args.num_instances, args.instance_ids)
    print(f"[swebench] running {len(instances)} instances @ model={args.llm}", flush=True)

    if args.provider == "qwen":
        adapter = OpenAIResponsesAdapter(model=args.llm, provider="qwen")
    elif args.provider == "openai-responses":
        adapter = OpenAIResponsesAdapter(model=args.llm, provider="openai")
    else:
        adapter = OpenAIAdapter(
            model=args.llm,
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )

    predictions_path = RESULTS_DIR / f"predictions_{args.slice_id}.jsonl"
    summary_path = RESULTS_DIR / f"summary_{args.slice_id}.json"

    sem = asyncio.Semaphore(args.concurrency)
    results: list[InstanceResult] = []

    async def _worker(inst: dict) -> InstanceResult:
        async with sem:
            print(f"[start] {inst['instance_id']}", flush=True)
            r = await run_instance(
                inst,
                model=args.llm,
                max_iterations=args.max_iterations,
                adapter=adapter,
                docker_image=args.docker_image,
            )
            status = "OK" if not r.error and r.model_patch else "EMPTY" if not r.model_patch else "ERR"
            print(
                f"[done]  {r.instance_id}  status={status}  turns={r.turns}  "
                f"tool_calls={r.tool_calls}  elapsed={r.elapsed_s:.1f}s",
                flush=True,
            )
            if r.error:
                print(f"        error: {r.error[:200]}", flush=True)
            return r

    results = await asyncio.gather(*(_worker(inst) for inst in instances))

    with predictions_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(
                json.dumps(
                    {
                        "instance_id": r.instance_id,
                        "model_name_or_path": MODEL_NAME_OR_PATH,
                        "model_patch": r.model_patch,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    summary = {
        "slice_id": args.slice_id,
        "model": args.llm,
        "num_instances": len(results),
        "non_empty_patches": sum(1 for r in results if r.model_patch.strip()),
        "errors": sum(1 for r in results if r.error),
        "total_elapsed_s": sum(r.elapsed_s for r in results),
        "per_instance": [
            {
                "instance_id": r.instance_id,
                "turns": r.turns,
                "tool_calls": r.tool_calls,
                "elapsed_s": round(r.elapsed_s, 2),
                "patch_lines": len(r.model_patch.splitlines()),
                "error": r.error,
            }
            for r in results
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(
        f"\n[swebench] predictions → {predictions_path}\n"
        f"[swebench] summary     → {summary_path}\n"
        f"[swebench] {summary['non_empty_patches']}/{summary['num_instances']} "
        f"produced non-empty patches "
        f"(errors: {summary['errors']})"
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--num-instances", type=int, default=3, help="How many (head of parquet)")
    p.add_argument("--instance-ids", nargs="*", default=None, help="Specific instance_ids")
    p.add_argument("--slice-id", default="smoke", help="Tag for output filenames")
    p.add_argument("--llm", default="gpt-5.4")
    p.add_argument(
        "--provider",
        choices=["openai", "openai-responses", "qwen"],
        default="openai",
        help="openai = chat.completions, openai-responses = /v1/responses (gpt-5.4 w/ reasoning), "
             "qwen = DashScope Responses endpoint (DASHSCOPE_API_KEY).",
    )
    p.add_argument("--concurrency", type=int, default=1,
                   help="NOTE: must be 1 — we os.chdir per instance so parallel tasks would race. "
                        "To parallelize, run multiple processes with disjoint --instance-ids.")
    p.add_argument("--max-iterations", type=int, default=60)
    p.add_argument(
        "--docker-image",
        default=None,
        help="If set, start a Linux container per instance (repo bind-mounted at /repo) "
             "and expose Bash inside it so the agent can `pip install -e .` and run pytest "
             "to verify its fix. Example: python:3.11-bookworm",
    )
    args = p.parse_args(argv)

    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
