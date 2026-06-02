"""Playwright UI smoke for P12.3 AskUserQuestion card.

Drives a live server with an injected fake adapter that:
  - turn 1: calls AskUserQuestion with two options
  - turn 2: reads the user's reply and emits a short text reply

The UI should render a question card with two clickable options + a
free-text input. Clicking an option must POST `/api/user_questions/{id}`
and the adapter's second turn must observe the user's tool result.

No live LLM tokens consumed.
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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RESULTS = ROOT / "tests" / "results" / "p12_ask_user_question_ui"


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


def _write_config(run_dir: Path) -> Path:
    cfg = run_dir / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "app.yaml").write_text(
        """
active_profile: smoke
profiles:
  smoke: {}
active_kbs: []
knowledge_bases: []
runtime:
  mode: inline
  monitor:
    enabled: false
    wake_on_task_complete: false
    heartbeat_seconds: 30
""".strip(),
        encoding="utf-8",
    )
    (cfg / "models.yaml").write_text(
        """
profiles:
  smoke:
    llm:
      active: provider
      providers:
        provider:
          type: openai
          model: gpt-5-mini
          api_key_ref: smoke.llm.provider
""".strip(),
        encoding="utf-8",
    )
    return cfg


def _build_fake_adapter_module(run_dir: Path) -> Path:
    sc_dir = run_dir / "sitepatch"
    sc_dir.mkdir(parents=True, exist_ok=True)
    (sc_dir / "sitecustomize.py").write_text(
        """
from agent.core.loop import TextDelta, ToolUseDelta, TurnEnd


class _AskingAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.calls = 0

    async def stream(self, messages, tools, system=None, **options):
        self.calls += 1
        if self.calls == 1:
            yield ToolUseDelta(
                id='call-ask-1',
                name='AskUserQuestion',
                input_partial={
                    'question': 'Where should I save the report?',
                    'options': ['./out', '/tmp'],
                    'context': 'I need to know before writing the file.',
                },
            )
            yield TurnEnd(stop_reason='tool_use', usage={'total_tokens': 1})
            return
        yield TextDelta(text='ok, saving to your chosen location.')
        yield TurnEnd(stop_reason='end_turn', usage={'total_tokens': 1})


def _install():
    import agent.models.openai_responses_adapter as mod
    mod.OpenAIResponsesAdapter = _AskingAdapter
    import agent.ui.server as srv
    srv.resolve_api_key = lambda **_: 'smoke-key'


_install()
""".strip(),
        encoding="utf-8",
    )
    return sc_dir


def run(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    run_dir = RESULTS / _timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = _write_config(run_dir)
    sc_dir = _build_fake_adapter_module(run_dir)
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(__import__("os").environ)
    env["PYTHONPATH"] = (
        str(sc_dir) + __import__("os").pathsep + env.get("PYTHONPATH", "")
    )
    env["AGENT_CONFIG_DIR"] = str(cfg_dir)

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
        stdout=(run_dir / "server_stdout.txt").open("w", encoding="utf-8", errors="replace"),
        stderr=(run_dir / "server_stderr.txt").open("w", encoding="utf-8", errors="replace"),
        text=True,
    )

    summary: dict[str, Any] = {
        "passed": False,
        "base_url": base_url,
        "errors": [],
        "checks": {},
    }
    try:
        wait_for_server(base_url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()

            answer_calls: list[dict] = []

            def _on_request(request):
                if "/api/user_questions/" in request.url:
                    body = ""
                    try:
                        body = request.post_data or ""
                    except Exception:
                        body = ""
                    answer_calls.append({
                        "url": request.url,
                        "method": request.method,
                        "body": body,
                    })

            page.on("request", _on_request)
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#chat-send", timeout=10_000)

            # Send a prompt; the fake adapter will immediately call AskUserQuestion.
            page.fill("#chat-input", "Write a report for me")
            page.click("#chat-send")

            # Question card should appear.
            page.wait_for_selector(".user-question-card", timeout=15_000)
            card_text = page.locator(".user-question-card").last.inner_text(timeout=2_000)
            page.screenshot(path=str(run_dir / "01_question_card.png"), full_page=True)

            # Click the second option ("/tmp").
            option_buttons = page.locator(".user-question-option").all()
            tmp_button = next(
                (b for b in option_buttons if "/tmp" in b.inner_text()), None
            )
            assert tmp_button is not None, "/tmp option not found"
            tmp_button.click()

            # Wait for the card to be marked answered + assistant reply to appear.
            page.wait_for_selector(".user-question-card.is-answered", timeout=15_000)
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('.activity-bar')).some(el => ['done','error'].includes(el.dataset.status))",
                timeout=20_000,
            )
            page.screenshot(path=str(run_dir / "02_after_answer.png"), full_page=True)

            assistant_text = page.locator(".turn-assistant .assistant-text").last.inner_text(
                timeout=5_000
            )
            (run_dir / "assistant.txt").write_text(assistant_text, encoding="utf-8")

            browser.close()

        summary["checks"] = {
            "card_rendered": "Where should I save" in card_text,
            "context_shown": "before writing the file" in card_text,
            "options_visible": "./out" in card_text and "/tmp" in card_text,
            "answer_endpoint_hit": any(
                "/api/user_questions/" in call["url"] and call["method"] == "POST"
                for call in answer_calls
            ),
            "answer_body_carries_selection": any(
                "/tmp" in (call.get("body") or "") for call in answer_calls
            ),
            "assistant_responded_after_answer": bool(
                assistant_text and "saving" in assistant_text.lower()
            ),
        }
        summary["answer_calls"] = answer_calls
        summary["card_text"] = card_text
        summary["assistant_text"] = assistant_text
        summary["passed"] = all(summary["checks"].values())
        if not summary["passed"]:
            summary["errors"].append("UI checks failed: " + json.dumps(summary["checks"]))
    except Exception as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps({"passed": summary["passed"], "errors": summary["errors"]},
                     ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
