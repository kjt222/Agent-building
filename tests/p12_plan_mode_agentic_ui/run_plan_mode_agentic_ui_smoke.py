"""Playwright UI smoke for agentic exit_plan_mode (P12.4.x).

Scripted fake adapter:
  - turn 1: emit exit_plan_mode(plan='...').
  - turn 2 (after approval): emit Write(path, content).
  - turn 3: end with text.

We exercise two flows:
  A. Reject path — Plan card renders, click Reject; gate stays closed.
  B. Approve path — Plan card renders, click Approve; subsequent Write
     diff card surfaces and approves; the file actually gets created.
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

RESULTS = ROOT / "tests" / "results" / "p12_plan_mode_agentic_ui"


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


def _build_fake_adapter(run_dir: Path, target_file: Path) -> Path:
    sc = run_dir / "sitepatch"
    sc.mkdir(parents=True, exist_ok=True)
    (sc / "sitecustomize.py").write_text(
        f"""
from agent.core.loop import TextDelta, ToolUseDelta, TurnEnd


_TARGET = r"{str(target_file)}"


class _PlanAdapter:
    def __init__(self, model, api_key, base_url=None):
        self.model = model
        self.calls = 0

    async def stream(self, messages, tools, system=None, **options):
        self.calls += 1
        if self.calls == 1:
            yield TextDelta(text='Investigating workspace…')
            yield ToolUseDelta(
                id='call-plan-1',
                name='exit_plan_mode',
                input_partial={{
                    'plan': (
                        '1. Create demo.txt at the target path.\\n'
                        '2. Write a single line: hello plan mode.'
                    ),
                }},
            )
            yield TurnEnd(stop_reason='tool_use', usage={{'total_tokens': 1}})
            return
        if self.calls == 2:
            # Once approved, emit a Write call. If rejected, just end.
            # We don't know rejection state here — agent loop will only
            # re-enter this branch if the plan was approved (gate flipped).
            yield ToolUseDelta(
                id='call-write-1',
                name='Write',
                input_partial={{
                    'path': _TARGET,
                    'content': 'hello plan mode\\n',
                }},
            )
            yield TurnEnd(stop_reason='tool_use', usage={{'total_tokens': 1}})
            return
        yield TextDelta(text='done.')
        yield TurnEnd(stop_reason='end_turn', usage={{'total_tokens': 1}})


def _install():
    import agent.models.openai_responses_adapter as mod
    mod.OpenAIResponsesAdapter = _PlanAdapter
    import agent.ui.server as srv
    srv.resolve_api_key = lambda **_: 'smoke-key'


_install()
""".strip(),
        encoding="utf-8",
    )
    return sc


def _run_session(*, reject: bool, run_dir: Path, summary: dict) -> None:
    from playwright.sync_api import sync_playwright

    target_file = run_dir / ("created.txt" if not reject else "rejected.txt")
    cfg_dir = _write_config(run_dir)
    sc_dir = _build_fake_adapter(run_dir, target_file)
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(sc_dir) + os.pathsep + env.get("PYTHONPATH", "")
    env["AGENT_CONFIG_DIR"] = str(cfg_dir)
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
    label = "reject" if reject else "approve"
    try:
        wait_for_server(base_url)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
            page.goto(base_url, wait_until="domcontentloaded")
            page.wait_for_selector("#chat-send", timeout=10_000)
            # Turn on plan mode via the toggle.
            page.locator("#plan-mode-toggle").click()
            time.sleep(0.2)
            page.fill("#chat-input", "请规划如何在文件里写一行 hello plan mode。")
            page.click("#chat-send")
            page.wait_for_selector(".plan-preview-card", timeout=20_000)
            page.screenshot(path=str(run_dir / f"01_plan_card_{label}.png"), full_page=True)
            plan_card_text = page.locator(".plan-preview-card").last.inner_text(timeout=2_000)
            summary[f"{label}_card_text"] = plan_card_text

            if reject:
                page.locator(".plan-reject").last.click()
                page.wait_for_selector(".plan-preview-card.is-rejected", timeout=10_000)
                # Give the loop a moment to flush; the run will end with text
                # after the Write call is blocked by the still-closed gate.
                time.sleep(4.0)
                page.screenshot(path=str(run_dir / "02_after_reject.png"), full_page=True)
                summary["file_after_reject_exists"] = target_file.exists()
            else:
                page.locator(".plan-accept").last.click()
                page.wait_for_selector(".plan-preview-card.is-accepted", timeout=10_000)
                # The next turn should produce a Write diff card.
                page.wait_for_selector(".diff-preview-card", timeout=20_000)
                page.screenshot(path=str(run_dir / "02_diff_card_after_approve.png"), full_page=True)
                page.locator(".diff-accept").last.click()
                page.wait_for_selector(".diff-preview-card.is-accepted", timeout=10_000)
                time.sleep(4.0)
                page.screenshot(path=str(run_dir / "03_after_write.png"), full_page=True)
                summary["file_after_approve_exists"] = target_file.exists()
                if target_file.exists():
                    summary["file_content"] = target_file.read_text(encoding="utf-8")
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def run(_args: argparse.Namespace) -> int:
    run_root = RESULTS / _ts()
    run_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"passed": False, "errors": [], "checks": {}}
    try:
        # Phase A: reject.
        reject_dir = run_root / "reject"
        reject_dir.mkdir()
        _run_session(reject=True, run_dir=reject_dir, summary=summary)
        # Phase B: approve.
        approve_dir = run_root / "approve"
        approve_dir.mkdir()
        _run_session(reject=False, run_dir=approve_dir, summary=summary)

        summary["checks"] = {
            "reject_card_rendered": "hello plan mode" in (summary.get("reject_card_text") or ""),
            "reject_did_not_create_file": summary.get("file_after_reject_exists") is False,
            "approve_card_rendered": "hello plan mode" in (summary.get("approve_card_text") or ""),
            "approve_actually_wrote_file": summary.get("file_after_approve_exists") is True,
            "file_content_matches": (summary.get("file_content") or "").strip()
            == "hello plan mode",
        }
        summary["passed"] = all(summary["checks"].values())
        if not summary["passed"]:
            summary["errors"].append("checks failed: " + json.dumps(summary["checks"]))
    except Exception as exc:
        summary["errors"].append(f"{type(exc).__name__}: {exc}")
    finally:
        (run_root / "summary.json").write_text(
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
