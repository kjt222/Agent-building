from __future__ import annotations

import socket

import pytest

from agent.models.http_utils import assert_public_http_url


def test_rejects_non_http_scheme():
    with pytest.raises(RuntimeError, match="http/https"):
        assert_public_http_url("file:///etc/passwd")
    with pytest.raises(RuntimeError, match="http/https"):
        assert_public_http_url("gopher://example.com/")


def test_rejects_embedded_credentials():
    with pytest.raises(RuntimeError, match="credentials"):
        assert_public_http_url("http://user:pass@example.com/")


def test_rejects_loopback_literal():
    with pytest.raises(RuntimeError, match="non-public"):
        assert_public_http_url("http://127.0.0.1:9999/")
    with pytest.raises(RuntimeError, match="non-public"):
        assert_public_http_url("http://[::1]/")


def test_rejects_private_ip_literal():
    with pytest.raises(RuntimeError, match="non-public"):
        assert_public_http_url("http://10.0.0.1/path")
    with pytest.raises(RuntimeError, match="non-public"):
        assert_public_http_url("http://192.168.0.5/admin")


def test_rejects_link_local_metadata():
    with pytest.raises(RuntimeError, match="non-public"):
        assert_public_http_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_hostname_resolving_to_loopback(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(RuntimeError, match="non-public"):
        assert_public_http_url("http://internal.example.com/path")


def test_allows_public_ip_literal(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert_public_http_url("http://example.com/path")


def test_allow_localhost_flag(monkeypatch):
    assert_public_http_url("http://127.0.0.1:8765/", allow_localhost=True)


def test_unresolvable_host_rejected(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(RuntimeError, match="could not be resolved"):
        assert_public_http_url("http://no-such-host.invalid/")
