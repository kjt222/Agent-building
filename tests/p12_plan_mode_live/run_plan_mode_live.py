"""Real-model live validation for P12.4 Plan Mode.

Compares behaviour across two real models — gpt-5.4 (OpenAI) and
doubao-seed-2.0-code (Volcengine OpenAI-compatible) — to verify the
plan-mode contract converges:

  1. Model produces a numbered plan in plain text.
  2. Model does NOT mutate (Write/Edit/Bash/WordRuntimeEdit etc.) within
     the plan-mode turn.
  3. SSE `done` event carries `plan_mode_used: true`.

The prompt is deliberately natural-language ("帮我把存储层从 SQLite
迁移到 Postgres") with no tool hints. Capability differs between the two
models (gpt-5.4 reasons more; doubao-code may be more action-prone) but
both must respect the plan-mode block.

The script reuses the project's real config to read the actual API keys.
It rewrites `config/app.yaml`'s `active_profile` field for each case
(under a try/finally that restores the original value).
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

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RESULTS = ROOT / "tests" / "results" / "p12_plan_mode_live"
APP_YAML = ROOT / "config" / "app.yaml"


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last_exc = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/agent_runtime", timeout=3) as resp:
                if 200 <= resp.status < 300:
                    return
        except Exception as exc:
            last_exc = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not start in {timeout_s}s: {last_exc}")


def _swap_active_profile(target: str) -> str:
    """Rewrite `config/app.yaml`'s `active_profile:` field. Return the old value."""
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


def _parse_sse_events(blob: str) -> list[dict]:
    out: list[dict] = []
    event = ""
    data_lines: list[str] = []

    def flush():
        nonlocal event, data_lines
        if not data_lines:
            event = ""
            return
        data = "\n".join(data_lines)
        try:
            payload = json.loads(data)
        except Exception:
            payload = {"raw": data}
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
    res = requests.post(
        f"{base_url}/api/conversations",
        json={"title": title},
        timeout=10,
    )
    res.raise_for_status()
    body = res.json()
    return str(body.get("conversation_id") or body.get("id"))


def _run_plan_case(
    base_url: str,
    *,
    case_id: str,
    prompt: str,
    out_dir: Path,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    conv_id = _create_conversation(base_url, f"P12.4 plan {case_id}")
    case_dir = out_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "message": prompt,
        "mode": "restricted",
        "plan_mode": True,
        "conversation_id": conv_id,
        "history": [],
    }
    raw_blob: list[str] = []
    start = time.time()
    with requests.post(
        f"{base_url}/api/agent_chat_v2",
        json=payload,
        stream=True,
        timeout=timeout_s,
    ) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
            if chunk:
                raw_blob.append(chunk)
    raw = "".join(raw_blob)
    (case_dir / "raw_sse.txt").write_text(raw, encoding="utf-8")
    events = _parse_sse_events(raw)
    (case_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
        encoding="utf-8",
    )

    tokens = [e["data"].get("text", "") for e in events if e["event"] == "token"]
    activities = [e["data"] for e in events if e["event"] == "activity"]
    done = next((e["data"] for e in events if e["event"] == "done"), {})
    assistant_text = "".join(tokens)
    (case_dir / "assistant.txt").write_text(assistant_text, encoding="utf-8")

    # Did any *mutation* tool actually execute? We look for tool_call activities
    # whose tool name is in a known-mutation list. Plan mode should reject these
    # at the loop's plan gate; if any get through it is a real bug.
    mutating_names = {
        "Write", "Edit", "Bash", "WordRuntimeEdit",
        "ExcelRuntimeEdit", "PowerPointRuntimeEdit",
        "image_generate", "image_edit",
    }
    invoked_tools = [
        a.get("meta", {}).get("name")
        for a in activities
        if a.get("type") == "tool_call"
    ]
    invoked_tools = [t for t in invoked_tools if t]
    mutated = [t for t in invoked_tools if t in mutating_names]

    # Heuristic: did the model produce a numbered plan in its text?
    text_lower = assistant_text.strip()
    has_numbered_steps = any(
        line.lstrip().startswith(("1.", "1)", "①", "1、"))
        for line in text_lower.splitlines()
    )

    return {
        "case_id": case_id,
        "conversation_id": conv_id,
        "prompt": prompt,
        "elapsed_s": round(time.time() - start, 2),
        "assistant_text_preview": assistant_text[:600],
        "assistant_text_length": len(assistant_text),
        "invoked_tools": sorted(set(invoked_tools)),
        "mutated_tools": sorted(set(mutated)),
        "done": done,
        "checks": {
            "produced_plain_text_reply": bool(assistant_text.strip()),
            "no_mutation_tool_executed": not mutated,
            "plan_mode_used_in_done": done.get("plan_mode_used") is True,
            "model_emitted_numbered_plan": has_numbered_steps,
        },
        "case_dir": str(case_dir),
    }


def _start_server(out_dir: Path, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    return subprocess.Popen(
        [
            sys.executable,
            "-m", "uvicorn",
            "--factory",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--app-dir", str(ROOT),
            "agent.ui.server:create_app",
        ],
        env=env,
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
    parser.add_argument(
        "--prompt",
        default=(
            "我想把我们项目里的存储层从 SQLite 迁移到 Postgres。"
            "先帮我看看代码，然后给个迁移方案。"
        ),
    )
    args = parser.parse_args()
    out_dir = RESULTS / _timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Backup app.yaml so we always restore.
    backup = out_dir / "app.yaml.backup"
    shutil.copyfile(APP_YAML, backup)
    original_value: str | None = None

    summary: dict[str, Any] = {"passed": False, "cases": {}, "errors": []}
    try:
        for profile in [p.strip() for p in args.profiles.split(",") if p.strip()]:
            try:
                if original_value is None:
                    original_value = _swap_active_profile(profile)
                else:
                    _swap_active_profile(profile)
                port = find_free_port()
                base_url = f"http://127.0.0.1:{port}"
                proc = _start_server(out_dir, port)
                try:
                    wait_for_server(base_url)
                    case = _run_plan_case(
                        base_url,
                        case_id=f"plan_{profile.replace('.', '_')}",
                        prompt=args.prompt,
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

        # Convergence comparison.
        if len(summary["cases"]) >= 2 and not summary["errors"]:
            profiles = list(summary["cases"].keys())
            checks_per_profile = {
                p: summary["cases"][p].get("checks", {}) for p in profiles
            }
            same_pass_set = {
                p: tuple(sorted(k for k, v in checks_per_profile[p].items() if v))
                for p in profiles
            }
            summary["convergence"] = {
                "checks_per_profile": checks_per_profile,
                "both_passed": all(
                    summary["cases"][p].get("passed") for p in profiles
                ),
                "same_pass_set": len(set(same_pass_set.values())) == 1,
            }

        summary["passed"] = (
            all(c.get("passed") for c in summary["cases"].values())
            and not summary["errors"]
        )
    finally:
        if original_value:
            try:
                _swap_active_profile(original_value)
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
