"""Shared types for the ArtifactContext protocol (P11.3).

Artifacts are non-code binary documents (Word, Excel, KLayout) where the
in-context model must reason about format/layout/formula/visual state in
addition to text. Each artifact kind owns its own manifest dataclass —
we deliberately do not share a single schema, only the protocol below.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ArtifactKind(str, Enum):
    WORD = "word"
    EXCEL = "excel"
    KLAYOUT = "klayout"


class ArtifactManifest(Protocol):
    """Compact summary of one artifact, safe to inject into model context.

    Implementations are artifact-kind-specific dataclasses; this protocol
    is the only common surface they share.
    """

    kind: ArtifactKind
    path: str

    def to_dict(self) -> dict: ...
    def to_compact_text(self) -> str: ...


@dataclass
class ArtifactRecord:
    """One entry in the per-conversation ArtifactRegistry."""

    kind: ArtifactKind
    path: str
    manifest: ArtifactManifest
    last_updated_at: float

    @classmethod
    def now(cls, manifest: ArtifactManifest) -> "ArtifactRecord":
        return cls(
            kind=manifest.kind,
            path=manifest.path,
            manifest=manifest,
            last_updated_at=time.monotonic(),
        )
