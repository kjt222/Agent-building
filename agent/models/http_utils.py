from __future__ import annotations

import ipaddress
import json
import socket
from typing import Any, Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _is_public_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def assert_public_http_url(
    url: str,
    *,
    allow_localhost: bool = False,
    purpose: str = "fetch",
) -> None:
    """Reject schemes other than http/https and hosts that resolve to
    private/loopback/link-local/metadata addresses. Raises RuntimeError on
    rejection. Set ``allow_localhost`` only for explicit dev-server flows.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(
            f"{purpose} requires http/https url, got scheme {parsed.scheme!r}"
        )
    if parsed.username or parsed.password:
        raise RuntimeError(
            f"{purpose} url must not embed credentials"
        )
    host = parsed.hostname or ""
    if not host:
        raise RuntimeError(f"{purpose} url has no host")
    try:
        addr = ipaddress.ip_address(host)
        addresses = [addr]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise RuntimeError(f"{purpose} url host could not be resolved: {exc}")
        addresses = []
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not addresses:
            raise RuntimeError(f"{purpose} url host did not resolve to an IP")
    for addr in addresses:
        if addr.is_loopback and allow_localhost:
            continue
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_multicast
            or addr.is_reserved
            or addr.is_unspecified
        ):
            raise RuntimeError(
                f"{purpose} url host {host!r} resolves to non-public address {addr}"
            )


def _read_body(resp) -> str:
    try:
        raw = resp.read()
    except Exception:
        return ""
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def request_json(
    method: str,
    url: str,
    api_key: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Charset": "utf-8",
        "Authorization": f"Bearer {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=timeout) as resp:
            body = _read_body(resp)
    except HTTPError as exc:
        body = _read_body(exc)
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response from {url}: {body[:200]}") from exc


def stream_json(
    url: str,
    api_key: str,
    payload: Dict[str, Any],
    timeout: float = 60.0,
    extra_headers: Optional[Dict[str, str]] = None,
):
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept-Charset": "utf-8",
        "Authorization": f"Bearer {api_key}",
    }
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, headers=headers, method="POST")
    try:
        resp = urlopen(req, timeout=timeout)
    except HTTPError as exc:
        body = _read_body(exc)
        raise RuntimeError(f"HTTP {exc.code} for {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc

    with resp:
        for raw in resp:
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            try:
                yield json.loads(data_str)
            except json.JSONDecodeError:
                continue
