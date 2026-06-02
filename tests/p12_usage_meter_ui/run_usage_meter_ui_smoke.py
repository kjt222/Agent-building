"""Playwright UI smoke for the P12.6 token/cost meter.

Sends one chat turn through a fake adapter that reports known usage. After
the SSE 'done' fires, the meter chip should be visible in the header and
its text should contain the rolled-up token count.
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

RESULTS = ROOT / "tests" / "results" / "p12_usage_meter_ui"


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
from agent.core.loop import TextDelta, TurnEnd


class _FakeAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    async def stream(self, messages, tools, system=None, **options):
        yield TextDelta(text='hello')
        yield TurnEnd(
            stop_reason='end_turn',
            usage={
                'input_tokens': 1200,
                'output_tokens': 600,
                'reasoning_tokens': 0,
                'total_tokens': 1800,
            },
        )


def _install():
    import agent.models.openai_responses_adapter as mod
    mod.OpenAIResponsesAdapter = _FakeAdapter
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
            "-m", "uvicorn",
            "--factory",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--app-dir", str(ROOT),
            "agent.ui.server:create_app",
        ],
        env=env,
        cwd=str(ROOT),
        stdout=(run_dir / "server_stdout.txt").open("w", encoding="utf-8", errors="replace"),
        stderr=(run_dir / "server_stderr.txt").open("w", encoding="utf-8", errors="replace"),
        text=True,
    )
    summary: dict[str, Any] = {"passed": False, "errors": [], "checks": {}}
    try:
        wait_for_server(base_url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#chat-send", timeout=10_000)

            # Meter is hidden before any chat turn.
            meter_hidden_before = page.locator("#usage-meter").is_hidden()
            page.screenshot(path=str(run_dir / "01_before.png"), full_page=True)

            page.fill("#chat-input", "hi")
            page.click("#chat-send")

            # Wait for the meter to become visible (driven by SSE usage_update).
            page.wait_for_function(
                "() => !document.querySelector('#usage-meter').hidden",
                timeout=10_000,
            )
            meter_text_after = page.locator("#usage-meter").inner_text().strip()
            page.screenshot(path=str(run_dir / "02_after_one_run.png"), full_page=True)

            # Send a second turn to confirm cumulative increase.
            page.fill("#chat-input", "again")
            page.click("#chat-send")
            page.wait_for_function(
                "() => document.querySelector('#usage-meter').textContent.includes('3.60k')",
                timeout=10_000,
            )
            meter_text_cumulative = page.locator("#usage-meter").inner_text().strip()
            page.screenshot(path=str(run_dir / "03_cumulative.png"), full_page=True)

            browser.close()

        summary["meter_text_after_run1"] = meter_text_after
        summary["meter_text_after_run2"] = meter_text_cumulative
        summary["checks"] = {
            "meter_hidden_before_first_turn": bool(meter_hidden_before),
            "meter_shows_run1_total_1.80k": "1.80k tok" in meter_text_after,
            "meter_shows_cost_estimate": "$" in meter_text_after,
            "meter_shows_cumulative_3.60k_after_run2": "3.60k tok" in meter_text_cumulative,
        }
        summary["passed"] = all(summary["checks"].values())
        if not summary["passed"]:
            summary["errors"].append("checks failed: " + json.dumps(summary["checks"]))
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
    print(json.dumps({
        "passed": summary["passed"],
        "errors": summary["errors"],
        "checks": summary.get("checks"),
        "meter_text_after_run1": summary.get("meter_text_after_run1"),
        "meter_text_after_run2": summary.get("meter_text_after_run2"),
    }, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
