"""Playwright UI smoke for P12.2 diff preview card.

A scripted adapter calls Write on turn 1. The chat UI should render a
diff preview card with red/green hunks and Accept/Reject buttons.
- Run 1: click Accept; the Write must actually happen; assistant turn 2 fires.
- Run 2: click Reject; the Write must NOT happen; tool result is a rejection.
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


RESULTS = ROOT / "tests" / "results" / "p12_diff_preview_ui"


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


def _write_config(run_dir: Path, target_file: Path) -> Path:
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


def _build_fake_adapter_module(run_dir: Path, target_file: Path) -> Path:
    sc_dir = run_dir / "sitepatch"
    sc_dir.mkdir(parents=True, exist_ok=True)
    (sc_dir / "sitecustomize.py").write_text(
        f"""
from agent.core.loop import TextDelta, ToolUseDelta, TurnEnd


_TARGET = r"{str(target_file)}"


class _WriteAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.calls = 0

    async def stream(self, messages, tools, system=None, **options):
        self.calls += 1
        if self.calls == 1:
            yield ToolUseDelta(
                id='call-write-1',
                name='Write',
                input_partial={{
                    'path': _TARGET,
                    'content': 'hello\\nfrom\\nagent\\n',
                }},
            )
            yield TurnEnd(stop_reason='tool_use', usage={{'total_tokens': 1}})
            return
        yield TextDelta(text='done.')
        yield TurnEnd(stop_reason='end_turn', usage={{'total_tokens': 1}})


def _install():
    import agent.models.openai_responses_adapter as mod
    mod.OpenAIResponsesAdapter = _WriteAdapter
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
    target_file = run_dir / "out.txt"
    cfg_dir = _write_config(run_dir, target_file)
    sc_dir = _build_fake_adapter_module(run_dir, target_file)
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
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#chat-send", timeout=10_000)

            # --- Round 1: ACCEPT ----------------------------------------
            page.fill("#chat-input", "Please write a small file for me.")
            page.click("#chat-send")
            page.wait_for_selector(".diff-preview-card", timeout=15_000)
            card_text_accept = page.locator(".diff-preview-card").last.inner_text(
                timeout=2_000
            )
            page.screenshot(path=str(run_dir / "01_diff_card.png"), full_page=True)
            page.locator(".diff-accept").last.click()
            page.wait_for_selector(".diff-preview-card.is-accepted", timeout=10_000)
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('.activity-bar')).some(el => ['done','error'].includes(el.dataset.status))",
                timeout=20_000,
            )
            page.screenshot(path=str(run_dir / "02_after_accept.png"), full_page=True)
            target_after_accept = target_file.exists()
            target_content = (
                target_file.read_text(encoding="utf-8") if target_after_accept else ""
            )
            # Open a fresh chat so the second round produces a new card.
            page.click("#new-chat-btn")
            page.wait_for_selector("#chat-input")

            # --- Round 2: REJECT ----------------------------------------
            # First delete the file so we can prove the second Write was blocked.
            try:
                target_file.unlink()
            except FileNotFoundError:
                pass
            page.fill("#chat-input", "Please write again.")
            page.click("#chat-send")
            page.wait_for_selector(".diff-preview-card", timeout=15_000)
            page.locator(".diff-reject").last.click()
            page.wait_for_selector(".diff-preview-card.is-rejected", timeout=10_000)
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('.activity-bar')).some(el => ['done','error'].includes(el.dataset.status))",
                timeout=20_000,
            )
            page.screenshot(path=str(run_dir / "03_after_reject.png"), full_page=True)
            target_after_reject = target_file.exists()

            browser.close()

        summary["card_text_accept"] = card_text_accept
        summary["target_after_accept"] = target_after_accept
        summary["target_content_after_accept"] = target_content
        summary["target_after_reject"] = target_after_reject
        summary["checks"] = {
            "card_shows_path": str(target_file.name) in card_text_accept,
            "card_shows_add_marker": "+hello" in card_text_accept,
            "accept_actually_wrote_file": target_after_accept,
            "file_content_matches": target_content == "hello\nfrom\nagent\n",
            "reject_did_not_write_file": not target_after_reject,
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
