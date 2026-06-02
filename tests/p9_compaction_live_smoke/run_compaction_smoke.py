"""Live UI smoke for the P9.2 compaction-with-cheap-summary-adapter path.

Drives the running server through a subprocess uvicorn factory, sends a fake
oversized history that exceeds the configured token threshold, and asserts:

1. an ``activity / type=compaction`` SSE event fires;
2. the event reports ``model=gpt-5.4-mini`` (the configured cheap adapter);
3. ``compacted_tokens < original_tokens`` (real compression happened);
4. the agent_start ``runtime.compaction_summary.uses_primary_adapter`` is False.

The smoke writes its own custom config dir so the threshold can be lowered
(default production threshold is 100k tokens; we use 4k tokens here so the
test stays well under $0.05).

Run::

    python tests/p9_compaction_live_smoke/run_compaction_smoke.py

The command exits 0 on success, 1 on any assertion failure or transport
error. ``summary.json`` and ``raw_sse.txt`` land in
``tests/results/p9_compaction_live_smoke/<stamp>/``.
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
import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "tests" / "results" / "p9_compaction_live_smoke"


SUMMARY_MODEL = "gpt-5.4-mini"
SUMMARY_PROFILE = "gpt-5.4"
SUMMARY_API_KEY_REF = "gpt-5.4.llm.openai"
SUMMARY_BASE_URL = "https://api.openai.com/v1"

TOKEN_THRESHOLD = 4000   # lowered so the smoke is cheap
TRIGGER_RATIO = 0.5
PROTECTED_RECENT_MESSAGES = 4
PROTECTED_RECENT_TOKENS = 800

HISTORY_MESSAGES = 80
MESSAGE_CHARS = 600


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


def write_smoke_config(target_dir: Path) -> Path:
    """Copy the real config dir, then override ``app.yaml`` with smoke settings."""
    src = ROOT / "config"
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        dst = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)

    app_yaml = target_dir / "app.yaml"
    cfg = yaml.safe_load(app_yaml.read_text(encoding="utf-8")) or {}
    cfg.setdefault("agent", {})["compaction"] = {
        "enabled": True,
        "token_threshold": TOKEN_THRESHOLD,
        "trigger_ratio": TRIGGER_RATIO,
        "protected_recent_messages": PROTECTED_RECENT_MESSAGES,
        "protected_recent_tokens": PROTECTED_RECENT_TOKENS,
        "summary_max_tokens": 800,
        "summary_profile": SUMMARY_PROFILE,
        "summary_provider": "openai",
        "summary_provider_type": "openai",
        "summary_model": SUMMARY_MODEL,
        "summary_base_url": SUMMARY_BASE_URL,
        "summary_api_key_ref": SUMMARY_API_KEY_REF,
    }
    # Force the active profile to use a known-cheap path: the doubao-code
    # primary adapter is already configured in models.yaml. Compaction summary
    # uses a *different* adapter so the assertion makes sense.
    app_yaml.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return app_yaml


def build_synthetic_history() -> list[dict]:
    """Return ~HISTORY_MESSAGES messages, each MESSAGE_CHARS long.

    Generates dense pseudo-conversation content so the compactor's heuristic
    estimator (~2.5 chars/token) lands well above the configured trigger.
    """
    history: list[dict] = []
    base_text = (
        "discussing telemetry pipeline ingestion latency, exploring kafka "
        "partitioning rebalances, comparing zstd vs lz4 compression, evaluating "
        "the merits of adopting protobuf over msgpack, debating retention "
        "policies for the cold tier, planning a deprecation timeline. "
    )
    while len(base_text) < MESSAGE_CHARS:
        base_text += base_text
    base_text = base_text[:MESSAGE_CHARS]

    for i in range(HISTORY_MESSAGES):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({
            "role": role,
            "content": f"Turn {i}: {base_text}",
        })
    return history


def parse_sse_events(stream) -> list[dict]:
    events: list[dict] = []
    event_name = "message"
    data_lines: list[str] = []

    def flush():
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = "message"
            return
        text = "\n".join(data_lines)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = {"raw": text}
        events.append({"event": event_name, "data": payload})
        event_name = "message"
        data_lines = []

    for raw_line in stream.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        if raw_line == "":
            flush()
            continue
        if raw_line.startswith("event:"):
            event_name = raw_line.split(":", 1)[1].strip()
        elif raw_line.startswith("data:"):
            data_lines.append(raw_line.split(":", 1)[1].lstrip())
        # Stop reading once we have agent_start AND compaction events.
        compaction = next(
            (e for e in events
             if e["event"] == "activity" and (e["data"] or {}).get("type") == "compaction"),
            None,
        )
        start = next(
            (e for e in events
             if e["event"] == "activity" and (e["data"] or {}).get("type") == "agent_start"),
            None,
        )
        if compaction and start:
            break
    return events


def evaluate(events: list[dict]) -> dict:
    start_event = next(
        (e for e in events
         if e["event"] == "activity" and (e["data"] or {}).get("type") == "agent_start"),
        None,
    )
    compaction_event = next(
        (e for e in events
         if e["event"] == "activity" and (e["data"] or {}).get("type") == "compaction"),
        None,
    )
    failures: list[str] = []
    if start_event is None:
        failures.append("missing_agent_start_event")
    else:
        meta = ((start_event.get("data") or {}).get("meta") or {})
        runtime_meta = meta.get("runtime") or {}
        cs = runtime_meta.get("compaction_summary") or {}
        if cs.get("uses_primary_adapter") is True:
            failures.append("agent_start.runtime.compaction_summary.uses_primary_adapter is True")
        if str(cs.get("model")) != SUMMARY_MODEL:
            failures.append(f"agent_start expects model={SUMMARY_MODEL}, got={cs.get('model')!r}")
    if compaction_event is None:
        failures.append("missing_compaction_event")
    else:
        cdata = (compaction_event.get("data") or {}).get("meta") or {}
        if cdata.get("success") is not True:
            failures.append(f"compaction.success != True (error={cdata.get('error')!r})")
        if str(cdata.get("model")) != SUMMARY_MODEL:
            failures.append(f"compaction.model expects {SUMMARY_MODEL}, got={cdata.get('model')!r}")
        if int(cdata.get("compacted_tokens") or 0) >= int(cdata.get("original_tokens") or 0):
            failures.append(
                f"no token reduction: original={cdata.get('original_tokens')} "
                f"compacted={cdata.get('compacted_tokens')}"
            )
    return {
        "passed": not failures,
        "failures": failures,
        "agent_start": (start_event or {}).get("data"),
        "compaction": (compaction_event or {}).get("data"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir: Path = args.out_dir / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    config_dir = run_dir / "config"
    write_smoke_config(config_dir)

    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    print(f"[smoke] starting uvicorn factory at {base_url} with config={config_dir}")

    env = os.environ.copy()
    env["AGENT_CONFIG_DIR"] = str(config_dir)
    server_log = (run_dir / "server_stdout.txt").open("w", encoding="utf-8")
    server_err = (run_dir / "server_stderr.txt").open("w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [
            str(ROOT / ".venv" / "Scripts" / "python.exe"),
            "-m",
            "uvicorn",
            "agent.ui.server:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        stdout=server_log,
        stderr=server_err,
        text=True,
        env=env,
    )

    try:
        wait_for_server(base_url)
        history = build_synthetic_history()
        payload: dict[str, Any] = {
            "message": "Briefly summarize what we've discussed and pick one open question.",
            "history": history,
            "mode": "auto",
            "max_iterations": 1,
        }
        started = time.time()
        with requests.post(
            f"{base_url}/api/agent_chat_v2",
            json=payload,
            stream=True,
            timeout=(10, 180),
        ) as r:
            r.raise_for_status()
            events = parse_sse_events(r)
        elapsed = round(time.time() - started, 3)

        # Persist all SSE events for postmortem.
        (run_dir / "events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
            encoding="utf-8",
        )

        result = evaluate(events)
        result["elapsed_seconds"] = elapsed
        result["history_messages"] = len(history)
        result["base_url"] = base_url
        result["config_dir"] = str(config_dir)
        result["summary_model"] = SUMMARY_MODEL
        result["events_seen"] = len(events)

        (run_dir / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        marker = "PASS" if result["passed"] else "FAIL"
        print(f"[{marker}] compaction live smoke (elapsed={elapsed}s)")
        if result["failures"]:
            for fail in result["failures"]:
                print(f"  ! {fail}")
        ce = result.get("compaction") or {}
        cmeta = (ce.get("meta") or {}) if isinstance(ce, dict) else {}
        if cmeta:
            print(
                f"  compaction: {cmeta.get('original_tokens')} → "
                f"{cmeta.get('compacted_tokens')} tokens via {cmeta.get('model')}"
            )
        print(f"[summary] {run_dir / 'summary.json'}")
        return 0 if result["passed"] else 1
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_proc.kill()
            server_proc.wait(timeout=5)
        try:
            server_log.close()
            server_err.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
