"""Playwright UI smoke for P12.1 Stop button.

This smoke verifies the chat UI surface for the new interrupt path:

1. Send button starts as a green "Send".
2. When a run is streaming, the same button flips to a red "Stop ■".
3. Clicking Stop POSTs `/api/conversations/{id}/interrupt`.
4. After the SSE `done` event arrives, the button flips back to "Send".

To keep the smoke budget-free (no live LLM tokens) the server is launched
with a tiny in-process fake provider that streams slowly. Real provider
adapters are not exercised here; the loop / SSE / fetch path are. The
end-to-end cancel-through-AgentLoop behaviour is covered by the unit
suite (`test_agent_loop_interrupt.py`, `test_chat_interrupt_endpoint.py`).
"""

from __future__ import annotations

import argparse
import asyncio
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


RESULTS = ROOT / "tests" / "results" / "p12_stop_button_ui"


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
          model: smoke-fake
          api_key_ref: smoke.llm.provider
""".strip(),
        encoding="utf-8",
    )
    return cfg


def _build_fake_adapter_module(run_dir: Path) -> Path:
    """Write a sitecustomize that patches the OpenAI adapter to a slow fake.

    Loaded via PYTHONPATH so the subprocess server uses the fake without us
    needing to modify production code or a config switch.
    """
    sc_dir = run_dir / "sitepatch"
    sc_dir.mkdir(parents=True, exist_ok=True)
    (sc_dir / "sitecustomize.py").write_text(
        """
import asyncio

from agent.core.loop import TextDelta, TurnEnd


class _SlowFakeAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    async def stream(self, messages, tools, system=None, **options):
        # Stream slowly so the test can click Stop before completion.
        for chunk in ["Hello ", "from ", "the ", "fake ", "stream. "] * 6:
            await asyncio.sleep(0.4)
            yield TextDelta(text=chunk)
        yield TurnEnd(stop_reason='end_turn', usage={'total_tokens': 1})


def _install():
    import agent.models.openai_responses_adapter as mod
    mod.OpenAIResponsesAdapter = _SlowFakeAdapter
    # Also patch the resolver so missing API key doesn't 500 the request.
    import agent.ui.server as srv
    srv.resolve_api_key = lambda **_: 'smoke-key'


_install()
""".strip(),
        encoding="utf-8",
    )
    return sc_dir


def _set_active_profile_smoke(cfg_dir: Path) -> None:
    # The smoke config is in run_dir/config, not the repo's config/, so the
    # server must be pointed at it. create_app(cfg_root) takes the cfg dir.
    return


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
    # Point the app at our isolated config root.
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
            f"agent.ui.server:create_app",
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

            # Capture network requests to the interrupt endpoint.
            interrupt_calls: list[dict] = []

            def _on_request(request):
                if "/interrupt" in request.url:
                    interrupt_calls.append({
                        "url": request.url,
                        "method": request.method,
                    })

            page.on("request", _on_request)
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#chat-send", timeout=10_000)

            # State 1: button shows "Send".
            send_text_before = page.locator("#chat-send").inner_text().strip()
            page.screenshot(path=str(run_dir / "01_send_idle.png"), full_page=True)

            # Type a prompt and click Send.
            page.fill("#chat-input", "stream please")
            page.click("#chat-send")

            # State 2: while streaming, button must show "Stop" and is-stop class.
            page.wait_for_function(
                "() => document.querySelector('#chat-send')"
                ".classList.contains('is-stop')",
                timeout=10_000,
            )
            send_text_streaming = page.locator("#chat-send").inner_text().strip()
            page.screenshot(path=str(run_dir / "02_stop_visible.png"), full_page=True)

            # Click Stop — this posts /interrupt and lets the SSE finish flushing.
            page.click("#chat-send")

            # State 3: button returns to "Send" once the SSE 'done' event arrives.
            page.wait_for_function(
                "() => !document.querySelector('#chat-send')"
                ".classList.contains('is-stop')",
                timeout=20_000,
            )
            send_text_after = page.locator("#chat-send").inner_text().strip()
            page.screenshot(path=str(run_dir / "03_send_after.png"), full_page=True)

            activity_panel_text = ""
            try:
                activity_panel_text = page.locator(".activity-panel").last.inner_text(
                    timeout=2_000
                )
            except Exception:
                pass
            (run_dir / "activity_panel.txt").write_text(
                activity_panel_text, encoding="utf-8"
            )

            browser.close()

        summary["checks"] = {
            "send_text_before_is_send": send_text_before.lower().startswith("send"),
            "stop_button_active_while_streaming": send_text_streaming.lower().startswith("stop"),
            "send_text_after_is_send": send_text_after.lower().startswith("send"),
            "interrupt_endpoint_hit": any(
                call["method"] == "POST" and "/interrupt" in call["url"]
                for call in interrupt_calls
            ),
            "activity_contains_interrupted_marker": "Stopped" in activity_panel_text
            or "interrupted" in activity_panel_text.lower(),
        }
        summary["interrupt_calls"] = interrupt_calls
        summary["send_text_before"] = send_text_before
        summary["send_text_streaming"] = send_text_streaming
        summary["send_text_after"] = send_text_after
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
