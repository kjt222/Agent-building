"""Daemon idle sweeper + end-session lifecycle tests for the Word runtime."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agent.core.word_runtime import (
    WordRuntimeRequest,
    WordRuntimeResult,
    WordStructure,
)
from agent.core.word_runtime.session import WordRuntimeSessionManager


class CountingBackend:
    """Per-instance counters so we can observe lifecycle calls."""

    def __init__(self) -> None:
        self.connect_calls = 0
        self.shutdown_calls = 0
        self.alive = False

    name = "counting"

    def connect(self) -> None:
        self.connect_calls += 1
        self.alive = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive

    def get_structure(self, path: Path) -> WordStructure:
        return WordStructure()

    def apply(self, request: WordRuntimeRequest) -> WordRuntimeResult:
        return WordRuntimeResult(
            path=str(request.path),
            backup_path=None,
            ops_applied=0,
            structure_before={},
            structure_after={},
        )


def _backend_factory(holder: list[CountingBackend]):
    def _factory():
        b = CountingBackend()
        holder.append(b)
        return b

    return _factory


def test_daemon_sweeper_shuts_down_idle_session():
    backends: list[CountingBackend] = []
    manager = WordRuntimeSessionManager(_backend_factory(backends))
    manager.start_idle_sweeper(interval=0.05)

    session = manager.get_session("conv-x", idle_timeout=0.05)
    session.get_structure(Path("dummy.docx"))
    assert backends[-1].alive is True

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if backends[-1].shutdown_calls >= 1:
            break
        time.sleep(0.05)

    assert backends[-1].alive is False
    assert backends[-1].shutdown_calls == 1
    manager.shutdown_all()


def test_get_session_lazily_starts_sweeper():
    backends: list[CountingBackend] = []
    manager = WordRuntimeSessionManager(_backend_factory(backends))
    assert manager._sweeper_thread is None

    manager.get_session("conv-y")

    assert manager._sweeper_thread is not None
    assert manager._sweeper_thread.is_alive()
    manager.shutdown_all()


def test_shutdown_all_stops_sweeper_thread():
    backends: list[CountingBackend] = []
    manager = WordRuntimeSessionManager(_backend_factory(backends))
    manager.start_idle_sweeper(interval=0.05)
    thread = manager._sweeper_thread
    assert thread is not None and thread.is_alive()

    manager.shutdown_all()
    thread.join(timeout=2.0)
    assert thread.is_alive() is False


def test_end_session_drives_backend_shutdown():
    backends: list[CountingBackend] = []
    manager = WordRuntimeSessionManager(_backend_factory(backends))
    session = manager.get_session("conv-z")
    session.get_structure(Path("dummy.docx"))

    manager.end_session("conv-z")

    assert backends[-1].shutdown_calls == 1
    assert backends[-1].alive is False
    # End-session removes it from the manager but does not stop the daemon.
    assert manager._sweeper_thread is not None
    manager.shutdown_all()


def test_active_session_not_swept_within_idle_window():
    backends: list[CountingBackend] = []
    manager = WordRuntimeSessionManager(_backend_factory(backends))
    manager.start_idle_sweeper(interval=0.05)
    session = manager.get_session("conv-busy", idle_timeout=5.0)
    session.get_structure(Path("dummy.docx"))

    time.sleep(0.2)
    assert backends[-1].shutdown_calls == 0
    manager.shutdown_all()


def test_sweeper_survives_backend_exception():
    """A backend whose shutdown raises must not crash the daemon thread."""

    class FlakyBackend(CountingBackend):
        def shutdown(self) -> None:
            super().shutdown()
            raise RuntimeError("boom")

    holder: list[FlakyBackend] = []

    def _factory():
        b = FlakyBackend()
        holder.append(b)
        return b

    manager = WordRuntimeSessionManager(_factory)
    manager.start_idle_sweeper(interval=0.05)
    session = manager.get_session("conv-flaky", idle_timeout=0.05)
    session.get_structure(Path("dummy.docx"))

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if holder[-1].shutdown_calls >= 1:
            break
        time.sleep(0.05)

    assert holder[-1].shutdown_calls == 1
    assert manager._sweeper_thread is not None
    assert manager._sweeper_thread.is_alive() is True
    manager.shutdown_all()
