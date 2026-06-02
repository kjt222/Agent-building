"""Session manager + backend protocol for the Word runtime.

A session is per-conversation. We start one Word.Application (or equivalent
backend handle) on the first runtime op and reuse it for the conversation
lifetime, with an idle timeout. atexit ensures we never leak a WINWORD.EXE.
"""

from __future__ import annotations

import atexit
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from agent.core.word_runtime.types import (
    BackendUnavailable,
    NotConnected,
    WordRuntimeRequest,
    WordRuntimeResult,
    WordStructure,
)


DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0
DEFAULT_OP_TIMEOUT_SECONDS = 60.0
DEFAULT_SWEEP_INTERVAL_SECONDS = 30.0


class WordRuntimeBackend(Protocol):
    """Backend contract that ComWordBackend / UnoWordBackend both implement."""

    name: str

    def connect(self) -> None: ...
    def shutdown(self) -> None: ...
    def is_alive(self) -> bool: ...

    def get_structure(self, path: Path) -> WordStructure: ...
    def apply(self, request: WordRuntimeRequest) -> WordRuntimeResult: ...


@dataclass
class _SessionEntry:
    backend: WordRuntimeBackend
    last_used: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)


class WordRuntimeSession:
    """One backend instance scoped to a conversation_id (or 'default')."""

    def __init__(
        self,
        backend_factory: Callable[[], WordRuntimeBackend],
        *,
        conversation_id: str = "default",
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    ):
        self._factory = backend_factory
        self.conversation_id = conversation_id
        self.idle_timeout = float(idle_timeout)
        self._entry: _SessionEntry | None = None
        self._mutex = threading.Lock()

    def _ensure_backend(self) -> _SessionEntry:
        with self._mutex:
            if self._entry is not None and self._entry.backend.is_alive():
                self._entry.last_used = time.monotonic()
                return self._entry
            backend = self._factory()
            backend.connect()
            self._entry = _SessionEntry(backend=backend)
            return self._entry

    def get_structure(self, path: Path) -> WordStructure:
        entry = self._ensure_backend()
        with entry.lock:
            try:
                return entry.backend.get_structure(path)
            finally:
                entry.last_used = time.monotonic()

    def apply(self, request: WordRuntimeRequest) -> WordRuntimeResult:
        entry = self._ensure_backend()
        with entry.lock:
            try:
                return entry.backend.apply(request)
            finally:
                entry.last_used = time.monotonic()

    def maybe_idle_shutdown(self) -> bool:
        with self._mutex:
            entry = self._entry
            if entry is None:
                return False
            if (time.monotonic() - entry.last_used) < self.idle_timeout:
                return False
            self._entry = None
        try:
            entry.backend.shutdown()
        except Exception:
            pass
        return True

    def shutdown(self) -> None:
        with self._mutex:
            entry = self._entry
            self._entry = None
        if entry is not None:
            try:
                entry.backend.shutdown()
            except Exception:
                pass


class WordRuntimeSessionManager:
    """Process-global registry of sessions keyed by conversation_id."""

    def __init__(self, backend_factory: Callable[[], WordRuntimeBackend] | None = None):
        self._factory = backend_factory
        self._sessions: dict[str, WordRuntimeSession] = {}
        self._mutex = threading.Lock()
        self._sweeper_thread: threading.Thread | None = None
        self._sweeper_stop = threading.Event()
        self._sweeper_interval = DEFAULT_SWEEP_INTERVAL_SECONDS
        atexit.register(self.shutdown_all)

    def configure_factory(self, factory: Callable[[], WordRuntimeBackend]) -> None:
        with self._mutex:
            self._factory = factory

    def get_session(
        self,
        conversation_id: str | None = None,
        *,
        idle_timeout: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    ) -> WordRuntimeSession:
        cid = (conversation_id or "default").strip() or "default"
        with self._mutex:
            existing = self._sessions.get(cid)
            if existing is not None:
                return existing
            if self._factory is None:
                raise BackendUnavailable(
                    "WordRuntimeSessionManager has no backend factory configured"
                )
            session = WordRuntimeSession(
                self._factory,
                conversation_id=cid,
                idle_timeout=idle_timeout,
            )
            self._sessions[cid] = session
        self._ensure_sweeper_running()
        return session

    def end_session(self, conversation_id: str | None) -> None:
        cid = (conversation_id or "default").strip() or "default"
        with self._mutex:
            session = self._sessions.pop(cid, None)
        if session is not None:
            session.shutdown()

    def sweep_idle(self) -> int:
        sweeps = 0
        with self._mutex:
            sessions = list(self._sessions.items())
        for cid, session in sessions:
            if session.maybe_idle_shutdown():
                sweeps += 1
        return sweeps

    def shutdown_all(self) -> None:
        self.stop_idle_sweeper()
        with self._mutex:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------ daemon sweeper

    def start_idle_sweeper(self, interval: float = DEFAULT_SWEEP_INTERVAL_SECONDS) -> None:
        """Start the background idle-sweeper thread if not already running."""
        with self._mutex:
            self._sweeper_interval = max(0.05, float(interval))
            if self._sweeper_thread is not None and self._sweeper_thread.is_alive():
                return
            self._sweeper_stop.clear()
            thread = threading.Thread(
                target=self._sweep_loop,
                name="WordRuntimeIdleSweeper",
                daemon=True,
            )
            self._sweeper_thread = thread
            thread.start()

    def stop_idle_sweeper(self) -> None:
        with self._mutex:
            thread = self._sweeper_thread
            self._sweeper_thread = None
        if thread is None:
            return
        self._sweeper_stop.set()
        thread.join(timeout=2.0)

    def _ensure_sweeper_running(self) -> None:
        if self._sweeper_thread is not None and self._sweeper_thread.is_alive():
            return
        self.start_idle_sweeper(self._sweeper_interval)

    def _sweep_loop(self) -> None:
        # Idle sweeper: wake every interval, sweep, exit when stop event is set
        # or atexit handlers tear down the manager. Exceptions are swallowed
        # because this is a daemon thread and must never crash the process.
        stop = self._sweeper_stop
        while not stop.wait(self._sweeper_interval):
            try:
                self.sweep_idle()
            except Exception:
                continue


_MANAGER: WordRuntimeSessionManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_session_manager(
    backend_factory: Callable[[], WordRuntimeBackend] | None = None,
) -> WordRuntimeSessionManager:
    """Return the process-global session manager (lazy).

    ``backend_factory`` is set-default-if-missing: it is only installed when no
    factory has been configured yet. Tests can pre-inject a fake by calling
    this with their fake factory before any production call configures COM.
    """
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = WordRuntimeSessionManager(backend_factory)
        elif backend_factory is not None and _MANAGER._factory is None:
            _MANAGER.configure_factory(backend_factory)
    return _MANAGER


def _require_connected(handle: Any) -> None:
    if handle is None:
        raise NotConnected("backend handle is not connected")
