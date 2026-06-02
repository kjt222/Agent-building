"""Per-conversation artifact registry.

The registry is the only stateful surface in the ArtifactContext protocol.
Tools that touch an artifact (WordRead, WordRuntimeEdit, ExcelRead in
P11.3.2, etc.) register/refresh the artifact's manifest here after a
successful op. The server-side request handler reads the registry for
the current conversation and injects a compact serialization into the
system prompt.

Two invariants this module is responsible for:

1. After any mutation tool succeeds, the manifest for the touched
   artifact reflects post-mutation state. The model never reads a
   stale manifest from the registry.
2. The serialized ``<artifacts>`` block fits within a per-turn byte
   budget. Overflow is handled by truncation with an explicit marker,
   never silently. The tiered verifier output and global token budget
   live in P11.3.3; this module only enforces a coarse byte cap.
"""

from __future__ import annotations

import threading
from typing import Dict, Iterable

from agent.core.artifact_context.types import (
    ArtifactKind,
    ArtifactManifest,
    ArtifactRecord,
)
from agent.core.artifact_context.excel_manifest import ExcelArtifactManifest
from agent.core.artifact_context.word_manifest import WordArtifactManifest


_DEFAULT_BUDGET_CHARS = 2000


class ArtifactRegistry:
    """Per-conversation registry of touched artifacts, keyed by path."""

    def __init__(self) -> None:
        self._records: Dict[str, ArtifactRecord] = {}
        self._lock = threading.Lock()

    def register(self, manifest: ArtifactManifest) -> ArtifactRecord:
        record = ArtifactRecord.now(manifest)
        with self._lock:
            self._records[record.path] = record
        return record

    def get(self, path: str) -> ArtifactRecord | None:
        with self._lock:
            return self._records.get(path)

    def remove(self, path: str) -> None:
        with self._lock:
            self._records.pop(path, None)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def all_records(self) -> list[ArtifactRecord]:
        with self._lock:
            return list(self._records.values())

    def serialize_for_context(self, budget_chars: int = _DEFAULT_BUDGET_CHARS) -> str:
        """Return the body for a ``<artifacts>`` block (or empty string).

        Most-recently-updated artifacts come first, since that's almost
        always what the model needs. Overflow truncation is marked
        explicitly so the model knows entries are missing.
        """
        with self._lock:
            records = sorted(
                self._records.values(),
                key=lambda r: r.last_updated_at,
                reverse=True,
            )
        if not records:
            return ""

        out: list[str] = []
        used = 0
        truncated_count = 0
        for rec in records:
            chunk = rec.manifest.to_compact_text()
            chunk_len = len(chunk) + 1  # +1 for trailing newline join
            if used + chunk_len > budget_chars:
                truncated_count = len(records) - len(out)
                break
            out.append(chunk)
            used += chunk_len

        if truncated_count > 0:
            out.append(
                f"<truncated count=\"{truncated_count}\"/>  "
                "More artifacts touched this session; oldest entries dropped to fit budget."
            )
        return "\n".join(out)


# ----------------------------------------------------------------------
# Process-wide registry lookup, keyed by conversation_id.

_REGISTRIES: Dict[str, ArtifactRegistry] = {}
_REGISTRIES_LOCK = threading.Lock()


def get_registry(conversation_id: str | None) -> ArtifactRegistry:
    """Return the registry for ``conversation_id`` (lazy-create)."""
    cid = (conversation_id or "default").strip() or "default"
    with _REGISTRIES_LOCK:
        reg = _REGISTRIES.get(cid)
        if reg is None:
            reg = ArtifactRegistry()
            _REGISTRIES[cid] = reg
        return reg


def reset_registry(conversation_id: str | None) -> None:
    """Drop the registry for ``conversation_id`` (used on conversation delete)."""
    cid = (conversation_id or "default").strip() or "default"
    with _REGISTRIES_LOCK:
        _REGISTRIES.pop(cid, None)


def reset_all_registries() -> None:
    """Drop every registry (for tests only)."""
    with _REGISTRIES_LOCK:
        _REGISTRIES.clear()


def serialize_for_context(
    conversation_id: str | None,
    budget_chars: int = _DEFAULT_BUDGET_CHARS,
) -> str:
    """Convenience: serialize the registry of one conversation."""
    return get_registry(conversation_id).serialize_for_context(budget_chars)


def register_word_artifact(
    conversation_id: str | None,
    path: str,
    structure: dict,
) -> ArtifactRecord:
    """Convenience: derive a Word manifest from a structure dict and store it."""
    manifest = WordArtifactManifest.from_structure(path, structure)
    return get_registry(conversation_id).register(manifest)


def register_excel_artifact_from_runtime(
    conversation_id: str | None,
    path: str,
    structure: dict,
) -> ArtifactRecord:
    """Convenience: derive Excel manifest from ExcelRuntimeEdit.structure_after."""
    manifest = ExcelArtifactManifest.from_runtime_structure(path, structure)
    return get_registry(conversation_id).register(manifest)


def register_excel_artifact_from_read(
    conversation_id: str | None,
    path: str,
    read_result: dict,
) -> ArtifactRecord:
    """Convenience: derive Excel manifest from ExcelRead's result dict.

    Use this only when no COM-driven structure is available. The resulting
    manifest is sparser (no chart counts, no named-range validity, no
    AutoCalc mode) and will be overwritten by the next runtime edit.
    """
    manifest = ExcelArtifactManifest.from_read_result(path, read_result)
    return get_registry(conversation_id).register(manifest)


def conversation_id_from_ctx(ctx: object) -> str:
    """Extract a conversation_id from a tool's LoopContext.

    Tools store the conversation id in ``ctx.scratch`` (set by the request
    handler before invoking the loop) or on the LoopConfig. This helper
    centralizes the lookup so every artifact-aware tool agrees on the key.
    """
    scratch = getattr(ctx, "scratch", None)
    if isinstance(scratch, dict):
        for key in ("conversation_id", "conv_id"):
            value = scratch.get(key)
            if value:
                return str(value)
    config = getattr(ctx, "config", None)
    if config is not None:
        cid = getattr(config, "conversation_id", None)
        if cid:
            return str(cid)
    return "default"
