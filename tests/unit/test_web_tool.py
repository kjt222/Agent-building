"""Unit tests for the rebuilt web_tool (mocked HTTP — no real network)."""

from __future__ import annotations

import pytest

import agent.tools_v2.web_tool as web_tool
from agent.core.loop import LoopConfig, LoopContext
from agent.tools_v2.web_tool import FetchURLTool, WebSearchTool


class _FakeResp:
    def __init__(self, text, *, status=200, ctype="text/html; charset=utf-8"):
        self.text = text
        self.status_code = status
        self.headers = {"Content-Type": ctype}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _ctx():
    return LoopContext(config=LoopConfig())


_PAGE_HTML = """
<html><head><title>T</title><style>.x{color:red}</style></head>
<body><script>var a=1;</script><h1>Hello</h1><p>World  body   text.</p></body></html>
"""

_DDG_HTML = """
<html><body>
<div class="result results_links">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">First Result</a>
  <a class="result__snippet">Snippet about A.</a>
</div>
<div class="result results_links">
  <a class="result__a" href="https://example.org/b">Second Result</a>
  <a class="result__snippet">Snippet about B.</a>
</div>
</body></html>
"""


@pytest.mark.asyncio
async def test_fetch_url_strips_html(monkeypatch):
    monkeypatch.setattr(web_tool, "_get", lambda url, **k: _FakeResp(_PAGE_HTML))
    res = await FetchURLTool().run({"url": "https://example.com"}, _ctx())
    assert res.is_error is False
    assert "Hello" in res.content
    assert "World body text." in res.content  # whitespace collapsed
    assert "<h1>" not in res.content and "var a=1" not in res.content


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http():
    res = await FetchURLTool().run({"url": "ftp://x/y"}, _ctx())
    assert res.is_error is True
    assert "http" in res.content.lower()


@pytest.mark.asyncio
async def test_fetch_url_honors_max_chars(monkeypatch):
    monkeypatch.setattr(
        web_tool, "_get", lambda url, **k: _FakeResp("x" * 500, ctype="text/plain")
    )
    res = await FetchURLTool().run(
        {"url": "https://e.com/big.txt", "max_chars": 100}, _ctx()
    )
    assert "truncated at 100" in res.content


@pytest.mark.asyncio
async def test_fetch_url_reports_network_error(monkeypatch):
    def boom(url, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(web_tool, "_get", boom)
    res = await FetchURLTool().run({"url": "https://down.example"}, _ctx())
    assert res.is_error is True
    assert "Fetch failed" in res.content


@pytest.mark.asyncio
async def test_web_search_parses_and_unwraps(monkeypatch):
    monkeypatch.setattr(web_tool, "_get", lambda url, **k: _FakeResp(_DDG_HTML))
    res = await WebSearchTool().run({"query": "example"}, _ctx())
    assert res.is_error is False
    # DDG redirect wrapper unwrapped to the real target.
    assert "https://example.com/a" in res.content
    assert "https://example.org/b" in res.content
    assert "First Result" in res.content
    assert "Snippet about A." in res.content


@pytest.mark.asyncio
async def test_web_search_respects_max_results(monkeypatch):
    monkeypatch.setattr(web_tool, "_get", lambda url, **k: _FakeResp(_DDG_HTML))
    res = await WebSearchTool().run({"query": "example", "max_results": 1}, _ctx())
    assert "https://example.com/a" in res.content
    assert "https://example.org/b" not in res.content


@pytest.mark.asyncio
async def test_web_search_empty_query():
    res = await WebSearchTool().run({"query": "  "}, _ctx())
    assert res.is_error is True


def test_factory_builds_real_web_tools_now():
    from agent.tools_v2.factory import build_tool

    assert type(build_tool("WebSearch", {})).__name__ == "WebSearchTool"
    assert type(build_tool("FetchURL", {})).__name__ == "FetchURLTool"
