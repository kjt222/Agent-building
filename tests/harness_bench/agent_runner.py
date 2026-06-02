"""Server lifecycle + single-prompt runner shared by all harness_bench tasks.

Reuses the SSE-parsing logic proven in tests/p15_desktop_agent_live/. Each task
calls `run_prompt(...)` and gets back a RunOutcome with the parsed tool trace.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any, Iterator

import requests

from .base import RunOutcome, classify_silent_handoff


ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, timeout_s: float = 45.0) -> None:
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
    raise RuntimeError(f"server did not start: {last}")


def _parse_sse(blob: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event = ""
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event, data_lines
        if not data_lines:
            event = ""
            return
        raw = "\n".join(data_lines)
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw}
        events.append({"event": event or "message", "data": data})
        event = ""
        data_lines = []

    for line in blob.splitlines():
        if line == "":
            flush()
        elif line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    flush()
    return events


def _parse_activity_detail(activity: dict[str, Any]) -> Any:
    detail = activity.get("detail")
    if not isinstance(detail, str) or "...<truncated>" in detail:
        return None
    try:
        return json.loads(detail)
    except Exception:
        return None


def _extract_tool_trace(activities: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    calls: dict[str, dict[str, Any]] = {}
    names: list[str] = []
    trace: list[dict[str, Any]] = []
    for activity in activities:
        atype = activity.get("type")
        meta = activity.get("meta") or {}
        if atype == "tool_call":
            call_id = str(meta.get("id") or "")
            name = str(meta.get("name") or "")
            names.append(name)
            calls[call_id] = {"id": call_id, "name": name, "input": meta.get("input") or {}}
        elif atype == "tool_result":
            call_id = str(meta.get("tool_use_id") or "")
            call = calls.get(call_id, {"id": call_id, "name": ""})
            trace.append({
                "id": call_id,
                "name": call.get("name") or "",
                "input": call.get("input") or {},
                "is_error": bool(meta.get("is_error")),
                "detail": str(activity.get("detail") or ""),
                "parsed": _parse_activity_detail(activity),
            })
    return names, trace


@contextlib.contextmanager
def server(out_dir: Path, port: int | None = None) -> Iterator[str]:
    """Start uvicorn in a subprocess; yield base_url; tear down on exit."""
    out_dir.mkdir(parents=True, exist_ok=True)
    port = port or _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("SMOKE_NO_APPROVER", "1")  # fast-fail unattended ask_user
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "agent.ui.server:create_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=(out_dir / "server_stdout.txt").open("w", encoding="utf-8"),
        stderr=(out_dir / "server_stderr.txt").open("w", encoding="utf-8"),
    )
    try:
        _wait_for_server(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _create_conversation(base_url: str, title: str, profile: str = "") -> str:
    payload: dict[str, Any] = {"title": title}
    if profile:
        payload["profile"] = profile
    response = requests.post(f"{base_url}/api/conversations", json=payload, timeout=10)
    response.raise_for_status()
    body = response.json()
    return str(body.get("conversation_id") or body.get("id"))


def run_prompt(
    base_url: str,
    prompt: str,
    *,
    profile: str = "",
    mode: str = "read-only",
    title: str = "harness_bench",
    max_iterations: int = 0,  # 0 = unlimited; caller can override per task
    timeout_s: float = 360.0,
    out_dir: Path | None = None,
) -> RunOutcome:
    """Send one prompt, parse SSE, return RunOutcome.

    If out_dir is provided, raw_sse.txt + events.jsonl are written there.
    """
    started = time.time()
    try:
        conv_id = _create_conversation(base_url, title, profile=profile)
    except Exception as exc:
        return RunOutcome(error=f"create_conversation: {type(exc).__name__}: {exc}",
                          elapsed_s=time.time() - started)

    payload: dict[str, Any] = {
        "conversation_id": conv_id,
        "message": prompt,
        "history": [],
        "mode": mode,
        "max_iterations": max_iterations,
    }
    if profile:
        payload["profile"] = profile

    raw_chunks: list[str] = []
    try:
        with requests.post(
            f"{base_url}/api/agent_chat_v2",
            json=payload, stream=True, timeout=timeout_s,
        ) as response:
            response.raise_for_status()
            response.encoding = "utf-8"
            for chunk in response.iter_content(chunk_size=None, decode_unicode=False):
                if chunk:
                    raw_chunks.append(chunk.decode("utf-8", errors="replace"))
    except Exception as exc:
        return RunOutcome(error=f"agent_chat_v2: {type(exc).__name__}: {exc}",
                          elapsed_s=time.time() - started)

    raw = "".join(raw_chunks)
    events = _parse_sse(raw)

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw_sse.txt").write_text(raw, encoding="utf-8")
        (out_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8",
        )

    activities = [e["data"] for e in events if e["event"] == "activity"]
    tool_calls, tool_trace = _extract_tool_trace(activities)
    manifest = next((a for a in activities if a.get("type") == "tool_manifest"), {})
    agent_start = next((a for a in activities if a.get("type") == "agent_start"), {})
    assistant_text = "".join(
        str(e["data"].get("text") or "") for e in events if e["event"] == "token"
    )
    done = next((e["data"] for e in events if e["event"] == "done"), {})

    outcome = RunOutcome(
        tool_calls=tool_calls,
        tool_trace=tool_trace,
        assistant_text=assistant_text,
        manifest_tools=sorted((manifest.get("meta") or {}).get("tools") or []),
        capability_scope=(agent_start.get("meta") or {}).get("capability_scope"),
        done=done,
        elapsed_s=round(time.time() - started, 2),
    )
    # P18.1.4: post-hoc classify the recorded trace with the same detectors
    # the server-side StopPolicy uses. Bench analytics break out failures by
    # silent-handoff root cause this way, even when no nudge fired.
    outcome.silent_handoff_flags = classify_silent_handoff(outcome)
    return outcome
