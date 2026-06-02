"""Playwright UI smoke for Conversation fork (P12.7).

Flow:
  1. Send a message; agent replies (fake adapter).
  2. Hover the user bubble, click ✎.
  3. Edit text, click "Fork & Send".
  4. New conversation appears in sidebar; only the edited user msg + new
     assistant reply are present (original is untouched).
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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RESULTS = ROOT / "tests" / "results" / "p12_fork_ui"


def _ts() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


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
    raise RuntimeError(f"server did not start: {last}")


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
  access_mode: restricted
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


def _build_fake_adapter(run_dir: Path) -> Path:
    sc = run_dir / "sitepatch"
    sc.mkdir(parents=True, exist_ok=True)
    (sc / "sitepatch.py").write_text("", encoding="utf-8")
    (sc / "sitecustomize.py").write_text(
        """
from agent.core.loop import Role, TextBlock, TextDelta, TurnEnd


def _latest_user_text(messages):
    for msg in reversed(messages or []):
        role = getattr(msg, 'role', None)
        if role == Role.USER or (isinstance(msg, dict) and msg.get('role') == 'user'):
            content = getattr(msg, 'content', None)
            if content is None and isinstance(msg, dict):
                content = msg.get('content')
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for block in content:
                    text = getattr(block, 'text', None)
                    if text:
                        parts.append(str(text))
                    elif isinstance(block, dict):
                        parts.append(str(block.get('text') or ''))
                if parts:
                    return ' '.join(parts)
    return ''


class _EchoAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.model = model

    async def stream(self, messages, tools, system=None, **options):
        latest_user = _latest_user_text(messages)
        yield TextDelta(text=f'Echo: {latest_user[:80]}')
        yield TurnEnd(stop_reason='end_turn', usage={'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2})


def _install():
    import agent.models.openai_responses_adapter as mod
    mod.OpenAIResponsesAdapter = _EchoAdapter
    import agent.ui.server as srv
    srv.resolve_api_key = lambda **_: 'smoke-key'


_install()
""".strip(),
        encoding="utf-8",
    )
    return sc


def run(_args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    run_dir = RESULTS / _ts()
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = _write_config(run_dir)
    sc_dir = _build_fake_adapter(run_dir)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(sc_dir) + os.pathsep + env.get("PYTHONPATH", "")
    env["AGENT_CONFIG_DIR"] = str(cfg_dir)
    # Use an isolated SQLite DB so sidebar counts are not polluted by the
    # project-wide conversation store.
    env["AGENT_DB_PATH"] = str(run_dir / "agent.db")

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "--factory",
            "--host", "127.0.0.1", "--port", str(port),
            "--app-dir", str(ROOT), "agent.ui.server:create_app",
        ],
        env=env, cwd=str(ROOT),
        stdout=(run_dir / "server_stdout.txt").open("w", encoding="utf-8", errors="replace"),
        stderr=(run_dir / "server_stderr.txt").open("w", encoding="utf-8", errors="replace"),
        text=True,
    )

    summary: dict[str, Any] = {"passed": False, "errors": [], "checks": {}}
    try:
        wait_for_server(base_url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#chat-send", timeout=10_000)

            original_prompt = "请帮我写一个排序函数"
            page.fill("#chat-input", original_prompt)
            page.click("#chat-send")
            # Wait for the assistant bubble to land in the DOM.
            page.wait_for_selector(
                ".turn-assistant .bubble", timeout=15_000
            )
            # The fork button shows on hover; for headless we can show it by
            # clicking the user turn — but click on the button itself works
            # because :hover is not actually required for click in Playwright.
            # The bubble must contain text now; sync_current poll happens at
            # 3s intervals so wait a beat for the id to be attached.
            time.sleep(3.5)
            forks_visible = page.locator(".turn-user .fork-btn").count()
            page.screenshot(path=str(run_dir / "01_before_fork.png"), full_page=True)

            page.locator(".turn-user .fork-btn").first.click(force=True)
            page.wait_for_selector(".fork-editor", timeout=5_000)
            page.locator(".fork-editor-text").fill("请帮我写一个查找函数")
            page.screenshot(path=str(run_dir / "02_fork_editor.png"), full_page=True)

            # Capture sidebar count before submit.
            before_conv_count = page.locator(".conv-item").count()
            page.locator(".fork-submit").click()
            # New conversation should be opened; wait for the new assistant
            # bubble to appear, echoing the edited text.
            page.locator(".turn-assistant .bubble", has_text="查找函数").first.wait_for(
                state="visible", timeout=30_000
            )
            time.sleep(2.5)  # let conversation list refresh
            after_conv_count = page.locator(".conv-item").count()
            new_turns_user = page.locator(".turn-user .bubble").count()
            assistant_texts = page.eval_on_selector_all(
                ".turn-assistant .bubble",
                "els => els.map(e => e.textContent)",
            )
            page.screenshot(path=str(run_dir / "03_after_fork.png"), full_page=True)
            browser.close()

        summary["checks"] = {
            "fork_button_appeared": forks_visible >= 1,
            "sidebar_added_new_conv": after_conv_count == before_conv_count + 1,
            "new_conversation_has_one_user_msg": new_turns_user == 1,
            "new_conversation_echoes_edited_text": any(
                "查找函数" in t for t in assistant_texts
            ),
        }
        summary["passed"] = all(summary["checks"].values())
        summary["sidebar_before"] = before_conv_count
        summary["sidebar_after"] = after_conv_count
        summary["assistant_texts"] = assistant_texts
    except Exception as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        (run_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({
        "passed": summary["passed"],
        "errors": summary["errors"],
        "checks": summary.get("checks"),
    }, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
