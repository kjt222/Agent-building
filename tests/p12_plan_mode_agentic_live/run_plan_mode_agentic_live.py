"""Real-model live convergence for agentic exit_plan_mode (P12.4.x).

Same harness as P12.4's plan_mode_live runner, but with a prompt that
explicitly instructs the model to call ``exit_plan_mode`` once it has a
plan. We then assert that *both* doubao-code and gpt-5.4 actually invoke
that tool — proving the contract converges across capability.

We also assert no mutation tool fires before approval (the agent run
auto-rejects in headless mode because there is no UI; the plan handler
times out → tool returns is_error → loop ends without unlocking).

Budget: 2 turns per profile, max ~600 input tokens. Far under the live
test budget cap.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

import re
import threading

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RESULTS = ROOT / "tests" / "results" / "p12_plan_mode_agentic_live"
APP_YAML = ROOT / "config" / "app.yaml"

PROMPT = (
    "我有一个 Python 项目，目录里有 agent/ 和 docs/ 两个子文件夹。"
    "请帮我规划如何把 README 里"
    "「快速开始」这一节翻译成英文。你必须先用 Read/Glob/Grep 之类的只读工具"
    "看一下当前 README 的结构，确认要翻译的范围；想清楚之后必须调用 "
    "exit_plan_mode 工具把 plan 提交给我审核，不要直接写文件、不要直接写新代码。"
)


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/agent_runtime", timeout=3) as resp:
                if 200 <= resp.status < 300:
                    return
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not start in {timeout_s}s: {last}")


def _swap_active_profile(target: str) -> str:
    text = APP_YAML.read_text(encoding="utf-8")
    lines = text.splitlines()
    old_value = ""
    found = False
    for i, line in enumerate(lines):
        if line.startswith("active_profile:"):
            old_value = line.split(":", 1)[1].strip()
            lines[i] = f"active_profile: {target}"
            found = True
            break
    if not found:
        raise RuntimeError("active_profile line not found in config/app.yaml")
    APP_YAML.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return old_value


def _parse_sse(blob: str) -> list[dict]:
    out: list[dict] = []
    event = ""
    data_lines: list[str] = []

    def flush():
        nonlocal event, data_lines
        if not data_lines:
            event = ""
            return
        try:
            payload = json.loads("\n".join(data_lines))
        except Exception:
            payload = {"raw": "\n".join(data_lines)}
        out.append({"event": event or "message", "data": payload})
        event = ""
        data_lines = []

    for line in blob.splitlines():
        if line == "":
            flush()
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    flush()
    return out


def _create_conversation(base_url: str, title: str) -> str:
    res = requests.post(f"{base_url}/api/conversations", json={"title": title}, timeout=10)
    res.raise_for_status()
    body = res.json()
    return str(body.get("conversation_id") or body.get("id"))


def _run_case(base_url: str, *, case_id: str, prompt: str, out_dir: Path) -> dict[str, Any]:
    conv_id = _create_conversation(base_url, f"P12.4.x agentic {case_id}")
    case_dir = out_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "message": prompt,
        "mode": "restricted",
        "plan_mode": True,
        "conversation_id": conv_id,
        "history": [],
        # The default cap (8) is too tight when the model does Read/Glob/Grep
        # investigation before calling exit_plan_mode. 16 gives both doubao
        # and gpt-5.4 room.
        "max_iterations": 16,
    }
    raw: list[str] = []
    rejected_plan_ids: set[str] = set()
    _plan_id_re = re.compile(r'"plan_id"\s*:\s*"([a-zA-Z0-9_-]+)"')

    def _maybe_reject_plan(payload_chunk: str) -> None:
        for match in _plan_id_re.finditer(payload_chunk):
            plan_id = match.group(1)
            if plan_id in rejected_plan_ids:
                continue
            rejected_plan_ids.add(plan_id)
            def _post():
                try:
                    requests.post(
                        f"{base_url}/api/plan_approvals/{plan_id}",
                        json={
                            "approved": False,
                            "revision_note": (
                                "Auto-reject from live convergence harness — "
                                "we just needed to confirm both models invoke "
                                "exit_plan_mode under plan mode."
                            ),
                        },
                        timeout=20,
                    )
                except Exception:
                    pass
            threading.Thread(target=_post, daemon=True).start()

    start = time.time()
    with requests.post(
        f"{base_url}/api/agent_chat_v2", json=payload, stream=True, timeout=600
    ) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
            if chunk:
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                raw.append(chunk)
                _maybe_reject_plan(chunk)
    blob = "".join(raw)
    (case_dir / "raw_sse.txt").write_text(blob, encoding="utf-8")
    events = _parse_sse(blob)
    (case_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
        encoding="utf-8",
    )

    activities = [e["data"] for e in events if e["event"] == "activity"]
    tokens = [e["data"].get("text", "") for e in events if e["event"] == "token"]
    done = next((e["data"] for e in events if e["event"] == "done"), {})
    assistant_text = "".join(tokens)
    (case_dir / "assistant.txt").write_text(assistant_text, encoding="utf-8")

    invoked_tools: list[str] = []
    plan_preview_seen = False
    for act in activities:
        if act.get("type") == "tool_call":
            name = (act.get("meta") or {}).get("name")
            if name:
                invoked_tools.append(name)
        if act.get("type") == "plan_preview":
            plan_preview_seen = True

    mutating = {
        "Write", "Edit", "Bash", "WordRuntimeEdit",
        "ExcelRuntimeEdit", "PowerPointRuntimeEdit",
        "image_generate", "image_edit",
    }
    mutated = [t for t in invoked_tools if t in mutating]
    exit_called = "exit_plan_mode" in invoked_tools

    return {
        "case_id": case_id,
        "conversation_id": conv_id,
        "elapsed_s": round(time.time() - start, 2),
        "assistant_text_length": len(assistant_text),
        "invoked_tools": sorted(set(invoked_tools)),
        "mutated_tools": sorted(set(mutated)),
        "done": done,
        "checks": {
            "exit_plan_mode_was_called": exit_called,
            "no_mutation_tool_executed": not mutated,
            "plan_preview_emitted": plan_preview_seen,
            "plan_mode_used_in_done": done.get("plan_mode_used") is True,
        },
        "case_dir": str(case_dir),
    }


def _start_server(out_dir: Path, port: int) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
            "--app-dir", str(ROOT), "agent.ui.server:create_app",
        ],
        env=dict(os.environ),
        cwd=str(ROOT),
        stdout=(out_dir / "server_stdout.txt").open("a", encoding="utf-8", errors="replace"),
        stderr=(out_dir / "server_stderr.txt").open("a", encoding="utf-8", errors="replace"),
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profiles",
        default="doubao-code,gpt-5.4",
        help="Comma-separated profiles to test in order.",
    )
    args = parser.parse_args()
    out_dir = RESULTS / _ts()
    out_dir.mkdir(parents=True, exist_ok=True)

    backup = out_dir / "app.yaml.backup"
    shutil.copyfile(APP_YAML, backup)
    original: str | None = None

    summary: dict[str, Any] = {"passed": False, "cases": {}, "errors": []}
    try:
        for profile in [p.strip() for p in args.profiles.split(",") if p.strip()]:
            try:
                if original is None:
                    original = _swap_active_profile(profile)
                else:
                    _swap_active_profile(profile)
                port = free_port()
                base_url = f"http://127.0.0.1:{port}"
                proc = _start_server(out_dir, port)
                try:
                    wait_for_server(base_url)
                    case = _run_case(
                        base_url,
                        case_id=f"plan_{profile.replace('.', '_')}",
                        prompt=PROMPT,
                        out_dir=out_dir,
                    )
                    case["profile"] = profile
                    case["passed"] = all(case["checks"].values())
                    summary["cases"][profile] = case
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception as exc:
                summary["errors"].append(f"{profile}: {type(exc).__name__}: {exc}")
                summary["cases"][profile] = {"passed": False, "error": str(exc)}

        if len(summary["cases"]) >= 2 and not summary["errors"]:
            both_called = all(
                (c.get("checks") or {}).get("exit_plan_mode_was_called")
                for c in summary["cases"].values()
            )
            both_safe = all(
                (c.get("checks") or {}).get("no_mutation_tool_executed")
                for c in summary["cases"].values()
            )
            summary["convergence"] = {
                "both_called_exit_plan_mode": both_called,
                "no_mutation_in_either": both_safe,
                "both_passed": all(c.get("passed") for c in summary["cases"].values()),
            }
        summary["passed"] = (
            all(c.get("passed") for c in summary["cases"].values())
            and not summary["errors"]
        )
    finally:
        if original:
            try:
                _swap_active_profile(original)
            except Exception as exc:
                summary["errors"].append(f"failed to restore profile: {exc}")
                shutil.copyfile(backup, APP_YAML)
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps({
        "passed": summary["passed"],
        "errors": summary["errors"],
        "cases": {
            p: {
                "checks": c.get("checks"),
                "invoked_tools": c.get("invoked_tools"),
                "mutated_tools": c.get("mutated_tools"),
                "len": c.get("assistant_text_length"),
            }
            for p, c in summary["cases"].items()
        },
        "convergence": summary.get("convergence"),
    }, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
