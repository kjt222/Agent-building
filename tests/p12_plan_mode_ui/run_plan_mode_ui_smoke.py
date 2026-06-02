"""Playwright UI smoke for Plan mode toggle + Execute CTA (P12.4).

Fake adapter; validates that the composer toggle visually flips, the
payload carries plan_mode=true, the system prompt the adapter receives
contains a <plan_mode> block, and after the run finishes the
"Execute Plan" CTA appears under the assistant bubble. Clicking the CTA
flips the toggle back off and prefills the composer.
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

RESULTS = ROOT / "tests" / "results" / "p12_plan_mode_ui"


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


def _build_fake_adapter_module(run_dir: Path, capture_path: Path) -> Path:
    sc_dir = run_dir / "sitepatch"
    sc_dir.mkdir(parents=True, exist_ok=True)
    (sc_dir / "sitecustomize.py").write_text(
        f"""
from agent.core.loop import TextDelta, TurnEnd


_CAPTURE = r"{str(capture_path)}"


class _PlanAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.model = model

    async def stream(self, messages, tools, system=None, **options):
        try:
            with open(_CAPTURE, 'w', encoding='utf-8') as fh:
                fh.write(system or '')
        except Exception:
            pass
        yield TextDelta(text='Plan:\\n\\n1. Read the storage module.\\n2. Add a column.\\n3. Update tests.')
        yield TurnEnd(stop_reason='end_turn', usage={{'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2}})


def _install():
    import agent.models.openai_responses_adapter as mod
    mod.OpenAIResponsesAdapter = _PlanAdapter
    import agent.ui.server as srv
    srv.resolve_api_key = lambda **_: 'smoke-key'


_install()
""".strip(),
        encoding="utf-8",
    )
    return sc_dir


def run(_args: argparse.Namespace) -> int:
    from playwright.sync_api import sync_playwright

    run_dir = RESULTS / _timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    capture_file = run_dir / "captured_system.txt"
    cfg_dir = _write_config(run_dir)
    sc_dir = _build_fake_adapter_module(run_dir, capture_file)
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["PYTHONPATH"] = (
        str(sc_dir) + os.pathsep + env.get("PYTHONPATH", "")
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
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#plan-mode-toggle", timeout=10_000)
            # Toggle is initially Off.
            initial_text = page.locator("#plan-mode-toggle").inner_text()
            page.click("#plan-mode-toggle")
            page.wait_for_function(
                "() => document.getElementById('plan-mode-toggle').classList.contains('is-on')",
                timeout=5_000,
            )
            after_on_text = page.locator("#plan-mode-toggle").inner_text()
            page.screenshot(path=str(run_dir / "01_plan_toggle_on.png"), full_page=True)

            page.fill("#chat-input", "请帮我做一次复杂的存储层重构。")
            page.click("#chat-send")
            # Wait for run to complete (Execute Plan CTA appears).
            page.wait_for_selector(".execute-plan-cta", timeout=30_000)
            cta_visible = page.locator(".execute-plan-cta").count()
            page.screenshot(path=str(run_dir / "02_plan_reply.png"), full_page=True)

            # Click Execute Plan; expect composer to prefill and toggle to flip off.
            page.locator(".execute-plan-btn").click()
            time.sleep(0.2)
            after_click_toggle_on = page.eval_on_selector(
                "#plan-mode-toggle",
                "el => el.classList.contains('is-on')",
            )
            composer_after = page.eval_on_selector("#chat-input", "el => el.value")
            page.screenshot(path=str(run_dir / "03_after_execute.png"), full_page=True)

            captured = capture_file.read_text(encoding="utf-8") if capture_file.exists() else ""
            browser.close()

        summary["initial_text"] = initial_text
        summary["after_on_text"] = after_on_text
        summary["composer_after"] = composer_after
        summary["captured_has_plan_block"] = "<plan_mode>" in captured
        summary["checks"] = {
            "toggle_off_by_default": "Off" in (initial_text or ""),
            "toggle_flips_on": "On" in (after_on_text or ""),
            "cta_appeared": cta_visible >= 1,
            "system_prompt_has_plan_block": summary["captured_has_plan_block"],
            "execute_resets_toggle": after_click_toggle_on is False,
            "execute_prefills_composer":
                "Execute the plan above" in (composer_after or ""),
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
    }, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
