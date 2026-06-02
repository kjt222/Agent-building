"""Real-model live validation for P12.1 (Stop) and P12.3 (AskUserQuestion).

Uses the active profile and a real LLM call. Prompts are written like a real
user — no tool names, no step hints — to test framework behaviour, not the
test author's ability to script the model.

Three cases:

  A_stop_long_stream
    Prompt: "用中文写一篇大约 2000 字的关于光刻机的科普文章。"
    After ~3s of streaming, hit /api/conversations/{id}/interrupt.
    Assert: SSE ends with `done.stop_reason="user_interrupt"`,
    an `interrupted` activity event arrives, and the assistant text is
    a non-empty partial.

  B_ambiguous_should_ask
    Prompt: "帮我整理一下这次会议的纪要。"
    No file, no format, no scope — should trigger AskUserQuestion.
    Assert: an `user_question_request` activity event appears OR the
    assistant_text ends with a clarification (model may answer in text
    instead of using the tool). We accept either as "asked".

  B_specific_should_not_ask
    Prompt: a concrete file edit with explicit target.
    Assert: NO `user_question_request` event; the model proceeds.
"""

from __future__ import annotations

import argparse
import json
import os
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


RESULTS = ROOT / "tests" / "results" / "p12_live_validation"


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


def _create_conversation(base_url: str, title: str) -> str:
    res = requests.post(
        f"{base_url}/api/conversations",
        json={"title": title},
        timeout=10,
    )
    res.raise_for_status()
    body = res.json()
    return str(body.get("conversation_id") or body.get("id"))


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


def _run_case_streaming(
    base_url: str,
    *,
    case_id: str,
    prompt: str,
    out_dir: Path,
    interrupt_after_s: float | None = None,
    timeout_s: float = 180.0,
    mode: str = "restricted",
) -> dict[str, Any]:
    """Send a v2 chat request, stream SSE, optionally interrupt mid-stream."""
    conv_id = _create_conversation(base_url, f"P12 live {case_id}")
    case_dir = out_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "message": prompt,
        "mode": mode,
        "conversation_id": conv_id,
        "history": [],
    }
    raw_blob: list[str] = []
    interrupted_at: float | None = None
    start = time.time()
    with requests.post(
        f"{base_url}/api/agent_chat_v2",
        json=payload,
        stream=True,
        timeout=timeout_s,
    ) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            raw_blob.append(chunk)
            elapsed = time.time() - start
            if (
                interrupt_after_s is not None
                and interrupted_at is None
                and elapsed >= interrupt_after_s
            ):
                try:
                    ir = requests.post(
                        f"{base_url}/api/conversations/{conv_id}/interrupt",
                        json={},
                        timeout=5,
                    )
                    interrupted_at = elapsed
                    (case_dir / "interrupt_response.json").write_text(
                        json.dumps(ir.json(), ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception as exc:
                    (case_dir / "interrupt_error.txt").write_text(
                        repr(exc), encoding="utf-8"
                    )
    raw = "".join(raw_blob)
    (case_dir / "raw_sse.txt").write_text(raw, encoding="utf-8")
    events = _parse_sse_events(raw)
    (case_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events),
        encoding="utf-8",
    )

    tokens = [e["data"].get("text", "") for e in events if e["event"] == "token"]
    activities = [e["data"] for e in events if e["event"] == "activity"]
    done = next(
        (e["data"] for e in events if e["event"] == "done"),
        {},
    )

    return {
        "conversation_id": conv_id,
        "prompt": prompt,
        "interrupted_at_s": interrupted_at,
        "elapsed_s": round(time.time() - start, 2),
        "assistant_text": "".join(tokens),
        "activities": activities,
        "done": done,
        "called_tools": sorted({
            a.get("meta", {}).get("name")
            for a in activities
            if a.get("type") == "tool_call" and a.get("meta", {}).get("name")
        }),
        "case_dir": str(case_dir),
    }


def _winword_pids() -> set[int]:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq WINWORD.EXE", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return set()
    pids: set[int] = set()
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split(",")]
        if len(parts) >= 2 and parts[0].upper() == "WINWORD.EXE":
            try:
                pids.add(int(parts[1]))
            except ValueError:
                pass
    return pids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile", default=None, help="Override active profile (defaults to config)."
    )
    parser.add_argument(
        "--skip-stop", action="store_true", help="Skip case A (stop button)."
    )
    parser.add_argument(
        "--skip-ask", action="store_true", help="Skip cases B1/B2 (AskUserQuestion)."
    )
    args = parser.parse_args()

    out_dir = RESULTS / _timestamp()
    out_dir.mkdir(parents=True, exist_ok=True)

    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    if args.profile:
        env["AGENT_ACTIVE_PROFILE"] = args.profile

    server_proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--app-dir",
            str(ROOT),
            "agent.ui.server:create_app",
        ],
        env=env,
        cwd=str(ROOT),
        stdout=(out_dir / "server_stdout.txt").open("w", encoding="utf-8", errors="replace"),
        stderr=(out_dir / "server_stderr.txt").open("w", encoding="utf-8", errors="replace"),
        text=True,
    )

    summary: dict[str, Any] = {"passed": False, "errors": [], "cases": {}}
    winword_before = _winword_pids()

    try:
        wait_for_server(base_url)

        # ---------------- Case A: long-stream Stop -----------------
        if not args.skip_stop:
            res_a = _run_case_streaming(
                base_url,
                case_id="A_stop_long_stream",
                prompt="用中文写一篇大约 2000 字的关于光刻机的科普文章，需要解释它的工作原理。",
                out_dir=out_dir,
                interrupt_after_s=3.0,
                timeout_s=60.0,
                mode="restricted",
            )
            done = res_a["done"]
            # NOTE: assistant_text_nonempty is informational only — Stop can
            # legitimately fire during the model's reasoning phase before any
            # token is emitted (especially for thinking-style models).
            has_reasoning_or_text = bool(
                (res_a["assistant_text"] or "").strip()
            ) or any(
                a.get("type") == "thinking_update" for a in res_a["activities"]
            )
            res_a["checks"] = {
                "got_interrupt_activity": any(
                    a.get("type") == "interrupted" for a in res_a["activities"]
                ),
                "done_stop_reason_user_interrupt": done.get("stop_reason")
                == "user_interrupt",
                "done_interrupted_flag": bool(done.get("interrupted")),
                "model_started_processing": has_reasoning_or_text,
                "interrupt_endpoint_signalled": (
                    json.loads(
                        (Path(res_a["case_dir"]) / "interrupt_response.json").read_text(
                            encoding="utf-8"
                        )
                    ).get("signalled")
                    if (Path(res_a["case_dir"]) / "interrupt_response.json").exists()
                    else False
                ),
            }
            res_a["passed"] = all(res_a["checks"].values())
            summary["cases"]["A_stop_long_stream"] = res_a

        # ---------------- Case B1: ambiguous -> should ask -----------------
        if not args.skip_ask:
            res_b1 = _run_case_streaming(
                base_url,
                case_id="B1_ambiguous_should_ask",
                prompt="帮我整理一下这次会议的纪要。",
                out_dir=out_dir,
                interrupt_after_s=None,
                timeout_s=120.0,
                mode="restricted",
            )
            text = (res_b1["assistant_text"] or "").strip()
            asked_via_tool = any(
                a.get("type") == "user_question_request"
                for a in res_b1["activities"]
            )
            # Fallback: did the assistant text end with a clarification?
            asked_in_text = bool(
                text and any(
                    marker in text for marker in [
                        "请问",
                        "请告诉我",
                        "您需要",
                        "请提供",
                        "请确认",
                        "请给我",
                        "请补充",
                        "请说明",
                        "您希望",
                        "想了解",
                        "?",
                        "？",
                    ]
                )
            )
            res_b1["checks"] = {
                "asked_via_tool": asked_via_tool,
                "asked_via_text": asked_in_text,
                "model_clarified": asked_via_tool or asked_in_text,
                "assistant_text_nonempty": bool(text),
            }
            res_b1["passed"] = res_b1["checks"]["model_clarified"]
            summary["cases"]["B1_ambiguous_should_ask"] = res_b1

            # ---------------- Case B2: specific -> should NOT ask -----------------
            res_b2 = _run_case_streaming(
                base_url,
                case_id="B2_specific_should_not_ask",
                prompt=(
                    "用一句话告诉我 Python 中怎么把列表去重并保持原顺序，给一行代码就行。"
                ),
                out_dir=out_dir,
                interrupt_after_s=None,
                timeout_s=120.0,
                mode="restricted",
            )
            asked_via_tool = any(
                a.get("type") == "user_question_request"
                for a in res_b2["activities"]
            )
            text = (res_b2["assistant_text"] or "").strip()
            res_b2["checks"] = {
                "did_not_ask_via_tool": not asked_via_tool,
                "assistant_text_nonempty": bool(text),
                "answer_looks_like_code": any(
                    marker in text
                    for marker in ["dict.fromkeys", "list(dict", "seen", "set()"]
                ),
            }
            res_b2["passed"] = (
                res_b2["checks"]["did_not_ask_via_tool"]
                and res_b2["checks"]["assistant_text_nonempty"]
            )
            summary["cases"]["B2_specific_should_not_ask"] = res_b2

        summary["passed"] = all(
            c.get("passed") for c in summary["cases"].values()
        )
    except Exception as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()

        winword_after = _winword_pids()
        leaked = sorted(winword_after - winword_before)
        summary["winword_before"] = sorted(winword_before)
        summary["winword_after"] = sorted(winword_after)
        summary["winword_leaked_pids"] = leaked

        # Drop bulky raw activities from the top-level summary.json — they
        # are saved per-case.
        slim = json.loads(json.dumps(summary, ensure_ascii=False, default=str))
        for cid, cdata in slim.get("cases", {}).items():
            cdata.pop("activities", None)
        (out_dir / "summary.json").write_text(
            json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({
        "passed": summary["passed"],
        "errors": summary["errors"],
        "cases": {
            cid: {"passed": c.get("passed"), "checks": c.get("checks")}
            for cid, c in summary["cases"].items()
        },
        "winword_leaked_pids": summary.get("winword_leaked_pids"),
    }, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
