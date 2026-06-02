"""Backend protocol + session manager tests using a fake backend.

Real COM is exercised by the smoke under tests/p11_word_runtime_smoke/.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent.core.word_runtime import (
    AnchorMode,
    BackendUnavailable,
    HeadingInfo,
    NotConnected,
    OpKind,
    UnknownAnchor,
    WordRuntimeOp,
    WordRuntimeRequest,
    WordRuntimeResult,
    WordRuntimeSession,
    WordStructure,
    get_session_manager,
)
from agent.core.word_runtime.session import WordRuntimeSessionManager


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.connected = False
        self.shutdown_calls = 0
        self.applied: list[WordRuntimeRequest] = []
        self.fail_apply: Exception | None = None

    def connect(self) -> None:
        self.connected = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        self.connected = False

    def is_alive(self) -> bool:
        return self.connected

    def get_structure(self, path: Path) -> WordStructure:
        return WordStructure(
            headings=[HeadingInfo(text="H1", level=1, paragraph_index=0)],
            paragraph_count=2,
        )

    def apply(self, request: WordRuntimeRequest) -> WordRuntimeResult:
        if self.fail_apply is not None:
            raise self.fail_apply
        self.applied.append(request)
        return WordRuntimeResult(
            path=str(request.path),
            backup_path=None,
            ops_applied=len(request.ops),
            structure_before={},
            structure_after={},
        )


def _make_session(idle: float = 60.0) -> tuple[FakeBackend, WordRuntimeSession]:
    backend = FakeBackend()
    session = WordRuntimeSession(lambda: backend, idle_timeout=idle)
    return backend, session


def test_session_lazy_connect_and_reuse(tmp_path):
    backend, session = _make_session()
    assert backend.connected is False

    session.get_structure(tmp_path / "any.docx")
    session.get_structure(tmp_path / "any.docx")

    assert backend.connected is True
    assert backend.shutdown_calls == 0


def test_session_idle_shutdown(tmp_path):
    backend, session = _make_session(idle=0.05)
    session.get_structure(tmp_path / "any.docx")
    assert backend.connected is True

    time.sleep(0.1)
    swept = session.maybe_idle_shutdown()

    assert swept is True
    assert backend.connected is False
    assert backend.shutdown_calls == 1


def test_session_shutdown_explicit(tmp_path):
    backend, session = _make_session()
    session.get_structure(tmp_path / "any.docx")
    session.shutdown()
    assert backend.connected is False
    assert backend.shutdown_calls == 1


def test_session_apply_propagates_request(tmp_path):
    backend, session = _make_session()
    request = WordRuntimeRequest(
        path=tmp_path / "doc.docx",
        ops=[
            WordRuntimeOp(
                op=OpKind.REFRESH_FIELDS,
                anchor_mode=AnchorMode.HEADING_TEXT,
            )
        ],
    )
    result = session.apply(request)
    assert result.ops_applied == 1
    assert backend.applied == [request]


def test_op_validate_rejects_missing_anchor():
    op = WordRuntimeOp(op=OpKind.REPLACE_IN_HEADING)
    with pytest.raises(UnknownAnchor):
        op.validate_for_anchor()


def test_op_validate_passes_for_field_refresh():
    op = WordRuntimeOp(op=OpKind.REFRESH_FIELDS)
    op.validate_for_anchor()


def test_session_manager_shares_session_per_conversation():
    manager = WordRuntimeSessionManager(lambda: FakeBackend())
    a = manager.get_session("conv-1")
    b = manager.get_session("conv-1")
    c = manager.get_session("conv-2")
    assert a is b
    assert a is not c
    manager.shutdown_all()


def test_session_manager_requires_factory():
    manager = WordRuntimeSessionManager()
    with pytest.raises(BackendUnavailable):
        manager.get_session("conv-1")


def test_session_manager_end_session_calls_shutdown():
    backend = FakeBackend()
    manager = WordRuntimeSessionManager(lambda: backend)
    sess = manager.get_session("conv-1")
    sess.get_structure(Path("dummy.docx"))
    manager.end_session("conv-1")
    assert backend.shutdown_calls == 1


def test_global_manager_returns_singleton():
    a = get_session_manager(lambda: FakeBackend())
    b = get_session_manager()
    assert a is b


def test_not_connected_marker_exists():
    assert issubclass(NotConnected, Exception)
