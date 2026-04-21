"""One-shot headless UI smoke for the /app redesign.

Run against a live uvicorn on :8765. Verifies the three bugs from
docs/implementation.md (2026-04-20 entry) are actually fixed in DOM,
not just in the source diff.
"""
from __future__ import annotations

import json
import sys
from playwright.sync_api import sync_playwright


def run() -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto("http://127.0.0.1:8765/", wait_until="networkidle")

        # -------- 1. conv list renders with real dates (not "Invalid Date") --------
        page.wait_for_selector(".conv-item", timeout=5000)
        subs = page.eval_on_selector_all(
            ".conv-item-sub",
            "els => els.map(e => e.textContent)",
        )
        invalid = [s for s in subs if "Invalid" in (s or "")]
        if invalid:
            errors.append(f"Invalid Date still present in conv-list: {invalid}")
        print("[conv dates]", subs)

        # -------- 2. clicking a historical conv loads messages --------
        conv_items = page.query_selector_all(".conv-item")
        target = None
        for item in conv_items:
            cid = item.get_attribute("data-conv-id") or ""
            # pick one known to have messages from the curl probe earlier
            if "conv_20260226_004002_b889ab" in cid:
                target = item
                break
        if not target and conv_items:
            target = conv_items[0]
        if not target:
            errors.append("no .conv-item to click")
        else:
            target.click()
            page.wait_for_selector(".stream .turn", timeout=5000)
            turn_count = page.eval_on_selector_all(
                ".stream .turn", "els => els.length"
            )
            welcome_visible = page.evaluate(
                "() => { const e = document.getElementById('chat-empty'); return !!(e && e.parentElement && e.parentElement.id === 'chat-stream'); }"
            )
            print(f"[conv click] turns={turn_count} welcome_in_stream={welcome_visible}")
            if turn_count == 0:
                errors.append("clicking conv loaded zero turns")
            if welcome_visible:
                errors.append("welcome still attached to stream after conv click")

        # -------- 3. .stream is a scrollable bounded box --------
        # Inject many dummy turns so content exceeds viewport, then check scrollability.
        page.evaluate(
            """
            const stream = document.querySelector('#chat-stream');
            for (let i = 0; i < 60; i++) {
              const turn = document.createElement('article');
              turn.className = 'turn turn-user';
              const bubble = document.createElement('div');
              bubble.className = 'bubble';
              bubble.textContent = 'filler line ' + i + ' — '.repeat(20);
              turn.appendChild(bubble);
              stream.appendChild(turn);
            }
            """
        )
        page.wait_for_timeout(100)
        metrics = page.evaluate(
            """
            () => {
              const stream = document.querySelector('#chat-stream');
              const side = document.querySelector('.side');
              const main = document.querySelector('.main');
              const shell = document.querySelector('.shell');
              return {
                stream_scrollHeight: stream.scrollHeight,
                stream_clientHeight: stream.clientHeight,
                stream_overflow: getComputedStyle(stream).overflowY,
                main_height: main.clientHeight,
                side_height: side.clientHeight,
                shell_height: shell.clientHeight,
                viewport_height: innerHeight,
                bodyScrollY: window.scrollY,
              };
            }
            """
        )
        print("[scroll metrics]", json.dumps(metrics, indent=2))

        if metrics["stream_scrollHeight"] <= metrics["stream_clientHeight"]:
            errors.append(
                f"stream content not overflowing stream box: "
                f"scroll={metrics['stream_scrollHeight']} client={metrics['stream_clientHeight']}"
            )
        if metrics["stream_clientHeight"] >= metrics["viewport_height"]:
            errors.append(
                f"stream bigger than viewport — head/composer being squashed: "
                f"stream={metrics['stream_clientHeight']} vp={metrics['viewport_height']}"
            )
        if metrics["side_height"] > metrics["viewport_height"] + 2:
            errors.append(
                f"sidebar taller than viewport: side={metrics['side_height']} vp={metrics['viewport_height']}"
            )
        if metrics["shell_height"] > metrics["viewport_height"] + 2:
            errors.append(
                f"shell taller than viewport: shell={metrics['shell_height']} vp={metrics['viewport_height']}"
            )

        # try to actually scroll the stream and confirm scrollTop moves
        page.evaluate(
            "document.querySelector('#chat-stream').scrollTo({top: 99999})"
        )
        top = page.evaluate(
            "document.querySelector('#chat-stream').scrollTop"
        )
        print(f"[scroll attempt] stream.scrollTop={top}")
        if top <= 0:
            errors.append("stream.scrollTop stayed 0 after scrollTo — not scrollable")

        browser.close()

    if errors:
        print("\n=== FAIL ===")
        for e in errors:
            print(" -", e)
        return 1
    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(run())
