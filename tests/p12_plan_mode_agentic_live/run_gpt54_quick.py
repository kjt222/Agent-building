"""Tight P12.4.x gpt-5.4 sanity check.

Issues a single prompt that does not need any read/glob investigation, so the
loop only needs 1-2 iterations: the model must respond by calling
exit_plan_mode directly. This is cheaper and faster than the full convergence
harness while still proving that gpt-5.4 can invoke the new tool.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "tests" / "results" / "p12_plan_mode_agentic_live"


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/agent_runtime", timeout=3) as r:
                if 200 <= r.status < 300:
                    return
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"server did not start: {last}")


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


def _swap_profile(target: str) -> str:
    cfg = ROOT / "config" / "app.yaml"
    text = cfg.read_text(encoding="utf-8")
    lines = text.splitlines()
    old = ""
    for i, line in enumerate(lines):
        if line.startswith("active_profile:"):
            old = line.split(":", 1)[1].strip()
            lines[i] = f"active_profile: {target}"
            break
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return old


def main() -> int:
    out_dir = RESULTS / f"{_ts()}_gpt54_quick"
    out_dir.mkdir(parents=True, exist_ok=True)

    original = _swap_profile("gpt-5.4")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
            "--app-dir", str(ROOT), "agent.ui.server:create_app",
        ],
        env=dict(os.environ), cwd=str(ROOT),
        stdout=(out_dir / "server_stdout.txt").open("w", encoding="utf-8", errors="replace"),
        stderr=(out_dir / "server_stderr.txt").open("w", encoding="utf-8", errors="replace"),
        text=True,
    )

    summary: dict = {}
    try:
        _wait_for_server(base_url)
        res = requests.post(f"{base_url}/api/conversations", json={"title": "gpt54 quick"}, timeout=10)
        res.raise_for_status()
        conv_id = res.json().get("conversation_id") or res.json().get("id")

        prompt = (
            "我想把项目里 README 的「快速开始」章节翻译成英文。"
            "你不需要先去看文件 —— 直接基于一般经验给我一个 3-5 步的计划，"
            "然后必须调用 exit_plan_mode 工具把 plan 字段提交。"
            "不要写文件、不要调用 Read/Glob/Grep。"
        )
        payload = {
            "message": prompt, "mode": "restricted", "plan_mode": True,
            "conversation_id": conv_id, "history": [], "max_iterations": 6,
        }

        raw: list[str] = []
        rejected: set[str] = set()
        pid_re = re.compile(r'"plan_id"\s*:\s*"([a-zA-Z0-9_-]+)"')

        def _maybe_reject(chunk: str) -> None:
            for m in pid_re.finditer(chunk):
                pid = m.group(1)
                if pid in rejected:
                    continue
                rejected.add(pid)
                def _post():
                    try:
                        requests.post(
                            f"{base_url}/api/plan_approvals/{pid}",
                            json={"approved": False, "revision_note": "auto-reject (sanity check)"},
                            timeout=20,
                        )
                    except Exception:
                        pass
                threading.Thread(target=_post, daemon=True).start()

        with requests.post(
            f"{base_url}/api/agent_chat_v2", json=payload, stream=True, timeout=480
        ) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
                if chunk:
                    if isinstance(chunk, bytes):
                        chunk = chunk.decode("utf-8", errors="replace")
                    raw.append(chunk)
                    _maybe_reject(chunk)
        blob = "".join(raw)
        (out_dir / "raw_sse.txt").write_text(blob, encoding="utf-8")
        events = _parse_sse(blob)
        (out_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8",
        )
        activities = [e["data"] for e in events if e["event"] == "activity"]
        done = next((e["data"] for e in events if e["event"] == "done"), {})
        invoked = [
            (a.get("meta") or {}).get("name")
            for a in activities if a.get("type") == "tool_call"
        ]
        invoked = [t for t in invoked if t]
        plan_card_seen = any(a.get("type") == "plan_preview" for a in activities)
        summary = {
            "invoked_tools": sorted(set(invoked)),
            "exit_plan_mode_was_called": "exit_plan_mode" in invoked,
            "plan_preview_emitted": plan_card_seen,
            "plan_mode_used": done.get("plan_mode_used"),
            "plan_approved": done.get("plan_approved"),
            "done": done,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        if original:
            _swap_profile(original)
        (out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("exit_plan_mode_was_called") else 1


if __name__ == "__main__":
    raise SystemExit(main())
