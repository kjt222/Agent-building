"""Live UI smoke for the P9.1 Skills system.

Drives the running server through real HTTP and a real Chromium browser to
prove that ``skills/<name>/SKILL.md`` actually controls which tools the v2
endpoint exposes and which prompt scope the activity stream reports. The
smoke aborts each request right after the ``tool_manifest`` activity is
received, so it costs zero model tokens but exercises the whole HTTP/SSE
stack the browser uses.

Run:

    python tests/p9_skills_live_smoke/run_skills_live_smoke.py --base-url http://127.0.0.1:8766

Or let it manage its own server with ``--start-server`` (default).
"""

from __future__ import annotations

import argparse
import json
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
DEFAULT_OUT = ROOT / "tests" / "results" / "p9_skills_live_smoke"


CASES = [
    {
        "id": "direct_question",
        "prompt": "你是什么模型？只回答一句。",
        "expect_scope": "direct",
        "expect_tools": [],
    },
    {
        "id": "office_word",
        "prompt": "Modify this Word document report.docx and render the result.",
        "expect_scope": "office_word",
        "expect_tools": ["Glob", "Read", "RenderDocument", "WordEdit", "WordRead"],
    },
    {
        "id": "office_excel",
        "prompt": "Modify this Excel workbook budget.xlsx and render the result.",
        "expect_scope": "office_excel",
        "expect_tools": ["ExcelEdit", "ExcelRead", "Glob", "Read", "RenderDocument"],
    },
    {
        "id": "image_generation",
        "prompt": "生成一张可爱的女性数字人形象，胸牌写 AIVA-01。",
        "expect_scope": "image_generation",
        "expect_tools": ["Image", "RenderDocument"],
    },
    {
        "id": "web_research",
        "prompt": "查一下 OpenAI API 最新的图像生成模型有哪些。",
        "expect_scope": "research",
        "expect_tools": ["FetchURL", "KnowledgeSearch", "WebSearch"],
    },
    {
        "id": "kb_only",
        "prompt": "只根据知识库解释光刻的定义。",
        "expect_scope": "knowledge",
        "expect_tools": ["Glob", "Grep", "KnowledgeSearch", "Read"],
    },
    {
        "id": "artifact",
        "prompt": "写一个贪吃蛇 HTML 文件并测试。",
        "expect_scope": "artifact",
        "expect_tools": [
            "Bash",
            "Edit",
            "Glob",
            "Grep",
            "Job",
            "KnowledgeIndex",
            "KnowledgeSearch",
            "Read",
            "RenderDocument",
            "Resource",
            "Verify",
            "Write",
        ],
    },
]


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
        # Stop reading once we have the two events we need.
        manifest = next(
            (e for e in events if e["event"] == "activity" and (e["data"] or {}).get("type") == "tool_manifest"),
            None,
        )
        start = next(
            (e for e in events if e["event"] == "activity" and (e["data"] or {}).get("type") == "agent_start"),
            None,
        )
        if manifest and start:
            break
    return events


def run_case(base_url: str, case: dict) -> dict:
    payload = {
        "message": case["prompt"],
        "history": [],
        "mode": "auto",
        "max_iterations": 1,
    }
    started = time.time()
    response: dict[str, Any] = {
        "id": case["id"],
        "prompt": case["prompt"],
        "expect_scope": case["expect_scope"],
        "expect_tools": case["expect_tools"],
    }
    try:
        with requests.post(
            f"{base_url}/api/agent_chat_v2",
            json=payload,
            stream=True,
            timeout=(10, 60),
        ) as r:
            response["status_code"] = r.status_code
            if r.status_code != 200:
                response["error"] = r.text[:500]
                response["passed"] = False
                return response
            events = parse_sse_events(r)
    except requests.RequestException as exc:
        response["error"] = f"{type(exc).__name__}: {exc}"
        response["passed"] = False
        return response

    response["events_seen"] = len(events)
    response["elapsed_seconds"] = round(time.time() - started, 3)

    manifest = next(
        (e for e in events if e["event"] == "activity" and (e["data"] or {}).get("type") == "tool_manifest"),
        None,
    )
    start = next(
        (e for e in events if e["event"] == "activity" and (e["data"] or {}).get("type") == "agent_start"),
        None,
    )
    actual_tools = sorted(((manifest or {}).get("data", {}).get("meta") or {}).get("tools") or []) if manifest else []
    actual_scope = ((start or {}).get("data", {}).get("meta") or {}).get("capability_scope") if start else None
    response["actual_tools"] = actual_tools
    response["actual_scope"] = actual_scope
    response["scope_match"] = actual_scope == case["expect_scope"]
    response["tools_match"] = actual_tools == sorted(case["expect_tools"])
    response["passed"] = response["scope_match"] and response["tools_match"]
    return response


def playwright_screenshot(base_url: str, prompt: str, out_dir: Path) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"available": False, "error": "playwright not installed"}
    started = time.time()
    artifact: dict[str, Any] = {"available": True, "prompt": prompt}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            page.goto(base_url, wait_until="networkidle")
            page.wait_for_selector("#chat-input", timeout=10_000)
            page.fill("#chat-input", prompt)
            page.click("#chat-send")
            try:
                page.wait_for_selector(".activity-bar", timeout=20_000)
            except Exception:
                pass
            time.sleep(3)
            try:
                page.locator(".activity-bar").first.click()
                page.wait_for_selector(".activity-panel", timeout=5_000)
            except Exception:
                pass
            shot = out_dir / "ui_activity_expanded.png"
            page.screenshot(path=str(shot), full_page=False)
            artifact["screenshot"] = str(shot.relative_to(ROOT))
            try:
                bar_text = page.locator(".activity-bar").first.text_content()
                artifact["activity_bar_text"] = (bar_text or "").strip()[:200]
            except Exception:
                artifact["activity_bar_text"] = None
            try:
                panel_text = page.locator(".activity-panel").first.text_content()
                artifact["activity_panel_text"] = (panel_text or "").strip()[:1000]
            except Exception:
                artifact["activity_panel_text"] = None
            browser.close()
    except Exception as exc:
        artifact["error"] = f"{type(exc).__name__}: {exc}"
    artifact["elapsed_seconds"] = round(time.time() - started, 3)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--start-server", action="store_true", default=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = args.out_dir / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    server_proc = None
    try:
        if args.base_url:
            base_url = args.base_url.rstrip("/")
        else:
            port = find_free_port()
            base_url = f"http://127.0.0.1:{port}"
            print(f"[smoke] starting uvicorn factory at {base_url}")
            server_log = run_dir / "server_stdout.txt"
            server_err = run_dir / "server_stderr.txt"
            log_h = server_log.open("w", encoding="utf-8")
            err_h = server_err.open("w", encoding="utf-8")
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
                stdout=log_h,
                stderr=err_h,
                text=True,
            )
            wait_for_server(base_url)

        results = [run_case(base_url, case) for case in CASES]
        ui_artifact = playwright_screenshot(
            base_url,
            "Modify this Word document report.docx and render the result.",
            run_dir,
        )
    finally:
        if server_proc is not None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait(timeout=5)
            try:
                log_h.close()
                err_h.close()
            except Exception:
                pass

    summary = {
        "created_at": stamp,
        "base_url": base_url,
        "cases": results,
        "ui_artifact": ui_artifact,
        "passed": all(item.get("passed") for item in results),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for item in results:
        marker = "PASS" if item.get("passed") else "FAIL"
        print(
            f"[{marker}] {item['id']:<18} scope={item.get('actual_scope')!r:<22} "
            f"tools={len(item.get('actual_tools') or [])}"
        )
    print(f"[summary] {run_dir / 'summary.json'}")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
