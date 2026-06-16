"""WebSearch / FetchURL — network read tools (rebuilt after the recovery loss).

Both are SAFE + parallel_safe: read-only network access, no local mutation.

- FetchURL: GET a single URL and return its readable text (HTML stripped via
  lxml, size-capped).
- WebSearch: keyless web search via the DuckDuckGo HTML endpoint, returning a
  ranked list of (title, url, snippet); follow up with FetchURL to read one.

Network egress follows the host's routing (e.g. a TUN proxy) transparently —
no API key is required. The original implementation + its tests were lost in
the D-drive-format recovery; this is a clean reimplementation to the obvious
functional contract (factory.py dispatches both names).
"""

from __future__ import annotations

import html as _html
import re
from urllib.parse import parse_qs, unquote, urlparse

from agent.core.loop import LoopContext, PermissionLevel, ToolResultBlock
from agent.tools_v2.primitives import _ToolBase

_UA = "Mozilla/5.0 (compatible; AgentBuilding/1.0)"
_TIMEOUT = 20.0


def _get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    data: dict | None = None,
):
    """Thin requests wrapper (patch point for tests). POSTs when ``data`` is
    given (the DuckDuckGo HTML endpoint only returns results for POST)."""
    import requests

    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    if data is not None:
        resp = requests.post(
            url, data=data, headers=h, timeout=_TIMEOUT, allow_redirects=True
        )
    else:
        resp = requests.get(
            url, params=params, headers=h, timeout=_TIMEOUT, allow_redirects=True
        )
    resp.raise_for_status()
    return resp


def _html_to_text(raw_html: str, max_chars: int) -> str:
    try:
        from lxml import html as lxml_html

        doc = lxml_html.fromstring(raw_html)
        for tag in doc.xpath("//script | //style | //noscript | //svg | //head"):
            parent = tag.getparent()
            if parent is not None:
                parent.remove(tag)
        text = doc.text_content()
    except Exception:
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw_html)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = _html.unescape(text)
    text = re.sub(r"[ \t\r\f]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[truncated at {max_chars} chars]"
    return text


class FetchURLTool(_ToolBase):
    name = "FetchURL"
    description = (
        "Fetch a single http(s) URL and return its readable text content "
        "(HTML tags stripped). Use to read a specific page, article, or API "
        "response. Non-http URLs, timeouts, and non-2xx responses return an "
        "error; binary/huge bodies are size-capped."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL."},
            "max_chars": {
                "type": "integer",
                "description": "Cap on returned characters (default 8000).",
                "default": 8000,
            },
        },
        "required": ["url"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        url = str(input.get("url") or "").strip()
        if not url:
            return self._err("`url` must be non-empty.")
        scheme = urlparse(url).scheme
        if scheme not in ("http", "https"):
            return self._err(
                f"Only http/https URLs are supported (got {scheme or 'no scheme'})."
            )
        max_chars = max(1, int(input.get("max_chars") or 8000))
        try:
            resp = _get(url)
        except Exception as exc:
            return self._err(f"Fetch failed for {url}: {type(exc).__name__}: {exc}")
        ctype = resp.headers.get("Content-Type", "") if resp.headers else ""
        body = resp.text or ""
        if "html" in ctype.lower() or body.lstrip()[:1] == "<":
            text = _html_to_text(body, max_chars)
        elif len(body) > max_chars:
            text = body[:max_chars] + f"\n...[truncated at {max_chars} chars]"
        else:
            text = body
        header = f"# {url}\n(status {resp.status_code}, {ctype or 'unknown type'})\n\n"
        return self._ok(header + (text or "(empty body)"))


class WebSearchTool(_ToolBase):
    name = "WebSearch"
    description = (
        "Search the web (keyless, via DuckDuckGo) and return the top results "
        "as a ranked list of title / URL / snippet. Use to discover sources, "
        "then read one with FetchURL. Returns an error if the search backend "
        "is unreachable."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {
                "type": "integer",
                "description": "Number of results (default 6, max 10).",
                "default": 6,
            },
        },
        "required": ["query"],
    }
    permission_level = PermissionLevel.SAFE
    parallel_safe = True

    async def run(self, input: dict, ctx: LoopContext) -> ToolResultBlock:
        query = str(input.get("query") or "").strip()
        if not query:
            return self._err("`query` must be non-empty.")
        max_results = max(1, min(10, int(input.get("max_results") or 6)))
        try:
            results = self._ddg_search(query, max_results)
        except Exception as exc:
            return self._err(f"Web search failed: {type(exc).__name__}: {exc}")
        if not results:
            return self._ok(f"No results for: {query}")
        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
        return self._ok("\n".join(lines))

    def _ddg_search(self, query: str, max_results: int) -> list[dict]:
        from lxml import html as lxml_html

        resp = _get(
            "https://html.duckduckgo.com/html/", data={"q": query, "b": ""}
        )
        doc = lxml_html.fromstring(resp.text)
        out: list[dict] = []
        for result in doc.xpath('//div[contains(@class,"result")]'):
            anchors = result.xpath('.//a[contains(@class,"result__a")]')
            if not anchors:
                continue
            title = anchors[0].text_content().strip()
            url = self._unwrap_ddg_url(anchors[0].get("href") or "")
            snip = result.xpath('.//a[contains(@class,"result__snippet")]')
            snippet = snip[0].text_content().strip() if snip else ""
            if title and url:
                out.append({"title": title, "url": url, "snippet": snippet})
            if len(out) >= max_results:
                break
        return out

    @staticmethod
    def _unwrap_ddg_url(href: str) -> str:
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            qs = parse_qs(parsed.query)
            if "uddg" in qs:
                return unquote(qs["uddg"][0])
        return href
