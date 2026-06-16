"""One-shot diagnostic: run a single harness task end-to-end with a long
client timeout and a model override, then dump the full tool trace + verify.

Usage:
    python -m tests.harness_bench._diag_run <task_module> [model]
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

import requests

from tests.harness_bench.agent_runner import server, _create_conversation, _parse_sse, _extract_tool_trace


def main() -> int:
    task_name = sys.argv[1] if len(sys.argv) > 1 else "task_04_add_canvas_node"
    model = sys.argv[2] if len(sys.argv) > 2 else "doubao-seed-1-6-flash-250828"
    mod = importlib.import_module(f"tests.harness_bench.{task_name}")

    state = mod.setup() or {}
    prompt = state.get("_prompt") or getattr(mod, "PROMPT", "")
    mode = getattr(mod, "MODE", "read-only")
    canvas = state.get("canvas")
    print(f"== {task_name} | model={model} | mode={mode}")
    print(f"== canvas: {canvas}")

    out_dir = Path("tests/harness_bench/bench_results/_diag") / task_name
    out_dir.mkdir(parents=True, exist_ok=True)

    with server(out_dir) as base_url:
        conv = _create_conversation(base_url, "diag", profile="doubao-code")
        payload = {
            "conversation_id": conv,
            "message": prompt,
            "history": [],
            "mode": mode,
            "max_iterations": 0,
            "model": model,
        }
        started = time.time()
        raw = []
        try:
            with requests.post(
                f"{base_url}/api/agent_chat_v2", json=payload, stream=True, timeout=600
            ) as r:
                r.raise_for_status()
                r.encoding = "utf-8"
                for chunk in r.iter_content(chunk_size=None, decode_unicode=False):
                    if chunk:
                        raw.append(chunk.decode("utf-8", errors="replace"))
        except Exception as exc:
            print(f"!! request error after {time.time()-started:.0f}s: {type(exc).__name__}: {exc}")
        elapsed = time.time() - started
        blob = "".join(raw)
        (out_dir / "raw_sse.txt").write_text(blob, encoding="utf-8")
        events = _parse_sse(blob)
        activities = [e["data"] for e in events if e["event"] == "activity"]
        names, trace = _extract_tool_trace(activities)
        manifest = next((a for a in activities if a.get("type") == "tool_manifest"), {})
        done = next((e["data"] for e in events if e["event"] == "done"), {})
        print(f"== elapsed {elapsed:.0f}s | tool calls ({len(names)}): {names}")
        print(f"== manifest tools: {sorted((manifest.get('meta') or {}).get('tools') or [])}")
        for t in trace:
            inp = t.get("input") or {}
            cmd = inp.get("command") or inp.get("path") or ""
            print(f"   - {t['name']:>6} err={t['is_error']} | {str(cmd)[:80]} | {str(t.get('detail') or '')[:80]}")
        print(f"== done event: {done}")

    # verify against end state (canvas still on disk; we skipped teardown)
    class _O:  # minimal outcome stand-in for verify()
        tool_calls = names
        tool_trace = trace
        assistant_text = "".join(str(e["data"].get("text") or "") for e in events if e["event"] == "token")
    try:
        passed, reason = mod.verify(_O(), state)
        print(f"== VERIFY: passed={passed} | {reason}")
    except Exception as exc:
        print(f"== VERIFY error: {type(exc).__name__}: {exc}")
    finally:
        if hasattr(mod, "teardown"):
            mod.teardown(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
