"""Word runtime backend.

Routes structure-touching .docx edits through a real Word engine instead of
raw XML mutation, so TOC field cache, numbering, cross-references, fields,
and styles.xml stay consistent.

Design (P11.1):
    WordRuntimeSession (per conversation_id, lazy, idle-timeout, atexit)
        ->
    WordRuntimeBackend protocol
        - ComWordBackend (pywin32, primary)
        - UnoWordBackend (deferred slice)

Public surface re-exported here. Heavy imports stay lazy so non-Windows /
non-pywin32 environments can import the module without crashing.
"""

from __future__ import annotations

from agent.core.word_runtime.types import (
    AnchorMode,
    BackendUnavailable,
    FileLockedByOther,
    HeadingInfo,
    NotConnected,
    OpKind,
    UnknownAnchor,
    WordHeading,
    WordRuntimeError,
    WordRuntimeOp,
    WordRuntimeRequest,
    WordRuntimeResult,
    WordStructure,
)
from agent.core.word_runtime.session import (
    WordRuntimeBackend,
    WordRuntimeSession,
    get_session_manager,
)


__all__ = [
    "AnchorMode",
    "BackendUnavailable",
    "FileLockedByOther",
    "HeadingInfo",
    "NotConnected",
    "OpKind",
    "UnknownAnchor",
    "WordHeading",
    "WordRuntimeBackend",
    "WordRuntimeError",
    "WordRuntimeOp",
    "WordRuntimeRequest",
    "WordRuntimeResult",
    "WordRuntimeSession",
    "WordStructure",
    "get_session_manager",
]
