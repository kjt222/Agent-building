"""Browser/render verification tool for P3 vision-in-the-loop groundwork."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.tools_v2.primitives import _ToolBase


class VerifyTool(_ToolBase):
    name = "Verify"
    description = (
        "Verify an HTML file or URL with a headless browser. Use after writing "
        "HTML/CSS/JS/frontend artifacts. Can press keys, click, wait, check DOM "
        "selectors/styles/text/overflow, collect console/page errors, and save "
        "a screenshot. Example assertion: {type:'style_equals', selector:"
        "'#game-over', property:'display', value:'none'}."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "HTML file path or http(s) URL to open",
            },
            "viewport": {
                "type": "object",
                "properties": {
                    "width": {"type": "integer", "default": 1280},
                    "height": {"type": "integer", "default": 720},
                },
            },
            "actions": {
                "type": "array",
                "description": "Actions: wait/click/press/fill",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "selector": {"type": "string"},
                        "key": {"type": "string"},
                        "text": {"type": "string"},
                        "ms": {"type": "integer"},
                    },
                    "required": ["type"],
                },
            },
            "assertions": {
                "type": "array",
                "description": (
                    "Assertions: selector_exists, selector_not_exists, "
                    "text_contains, style_equals, js_equals, "
                    "no_horizontal_overflow, no_console_errors"
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "selector": {"type": "string"},
                        "text": {"type": "string"},
                        "property": {"type": "string"},
                        "value": {},
                        "expression": {"type": "string"},
                    },
                    "required": ["type"],
                },
            },
            "screenshot_path": {
                "type": "string",
                "description": "Optional screenshot output path",
            },
            "timeout_ms": {"type": "integer", "default": 10000},
        },
        "required": ["target"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = False

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        try:
            result = await asyncio.to_thread(self._run_sync, input)
        except Exception as exc:
            return self._err(f"{type(exc).__name__}: {exc}")
        return self._ok(json.dumps(result, ensure_ascii=False, indent=2))

    def _run_sync(self, input: dict) -> dict:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("playwright is not installed") from exc

        target = str(input["target"])
        url = self._target_url(target)
        viewport = input.get("viewport") or {}
        width = int(viewport.get("width") or 1280)
        height = int(viewport.get("height") or 720)
        timeout_ms = int(input.get("timeout_ms") or 10000)
        screenshot_path = self._screenshot_path(input.get("screenshot_path"))

        console_errors: list[str] = []
        page_errors: list[str] = []
        assertions: list[dict[str, Any]] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_default_timeout(timeout_ms)
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text)
                if msg.type in {"error", "warning"}
                else None,
            )
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.goto(url, wait_until="load", timeout=timeout_ms)

            for action in input.get("actions") or []:
                self._apply_action(page, action)

            for assertion in input.get("assertions") or []:
                assertions.append(self._check_assertion(page, assertion, console_errors, page_errors))

            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_path), full_page=True)
            title = page.title()
            body_text_sample = page.locator("body").inner_text(timeout=timeout_ms)[:800]
            browser.close()

        ok = (
            all(item.get("ok") for item in assertions)
            and not console_errors
            and not page_errors
        )
        return {
            "ok": ok,
            "target": target,
            "url": url,
            "title": title,
            "viewport": {"width": width, "height": height},
            "assertions": assertions,
            "console_errors": console_errors,
            "page_errors": page_errors,
            "screenshot_path": str(screenshot_path),
            "body_text_sample": body_text_sample,
        }

    def _target_url(self, target: str) -> str:
        if target.startswith(("http://", "https://", "file://")):
            return target
        return Path(target).expanduser().resolve().as_uri()

    def _screenshot_path(self, value: Any) -> Path:
        if value:
            return Path(str(value))
        return Path("tmp") / "verify" / f"verify_{int(time.time() * 1000)}.png"

    def _apply_action(self, page, action: dict) -> None:
        kind = str(action.get("type") or "").lower()
        if kind == "wait":
            page.wait_for_timeout(int(action.get("ms") or 250))
        elif kind == "click":
            page.locator(str(action["selector"])).click()
        elif kind == "press":
            page.keyboard.press(str(action["key"]))
        elif kind == "fill":
            page.locator(str(action["selector"])).fill(str(action.get("text") or ""))
        else:
            raise ValueError(f"unknown Verify action type: {kind}")

    def _check_assertion(
        self,
        page,
        assertion: dict,
        console_errors: list[str],
        page_errors: list[str],
    ) -> dict:
        kind = str(assertion.get("type") or "").lower()
        result: dict[str, Any] = {"type": kind, "ok": False}
        try:
            if kind == "selector_exists":
                selector = str(assertion["selector"])
                count = page.locator(selector).count()
                result.update(selector=selector, actual=count, ok=count > 0)
            elif kind == "selector_not_exists":
                selector = str(assertion["selector"])
                count = page.locator(selector).count()
                result.update(selector=selector, actual=count, ok=count == 0)
            elif kind == "text_contains":
                selector = str(assertion.get("selector") or "body")
                expected = str(assertion["text"])
                actual = page.locator(selector).inner_text()
                result.update(
                    selector=selector,
                    expected=expected,
                    actual=actual[:400],
                    ok=expected in actual,
                )
            elif kind == "style_equals":
                selector = str(assertion["selector"])
                prop = str(assertion["property"])
                expected = str(assertion["value"])
                actual = page.locator(selector).evaluate(
                    "(el, prop) => getComputedStyle(el).getPropertyValue(prop)",
                    prop,
                )
                result.update(
                    selector=selector,
                    property=prop,
                    expected=expected,
                    actual=actual,
                    ok=actual == expected,
                )
            elif kind == "js_equals":
                expression = str(assertion["expression"])
                expected = assertion.get("value")
                actual = page.evaluate(expression)
                result.update(
                    expression=expression,
                    expected=expected,
                    actual=actual,
                    ok=actual == expected,
                )
            elif kind == "no_horizontal_overflow":
                actual = page.evaluate(
                    "document.documentElement.scrollWidth > "
                    "document.documentElement.clientWidth"
                )
                result.update(actual=actual, ok=actual is False)
            elif kind == "no_console_errors":
                result.update(
                    actual=console_errors + page_errors,
                    ok=not console_errors and not page_errors,
                )
            else:
                result.update(error=f"unknown Verify assertion type: {kind}")
        except Exception as exc:
            result.update(error=f"{type(exc).__name__}: {exc}")
        return result
