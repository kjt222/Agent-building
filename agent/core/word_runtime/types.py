"""Shared dataclasses, enums, and error taxonomy for the Word runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class OpKind(str, Enum):
    """Operations supported by the runtime backend.

    Slice 1: heading-anchored structural ops, field refresh, TOC creation, save.
    Excluded (raw text-in-paragraph) deliberately stays on the XML fast path.
    """

    REPLACE_IN_HEADING = "replace_in_heading"
    INSERT_PARAGRAPH_AFTER_HEADING = "insert_paragraph_after_heading"
    SET_HEADING_TEXT = "set_heading_text"
    REFRESH_FIELDS = "refresh_fields"
    ADD_TOC = "add_toc"
    SAVE_NORMALIZED = "save_normalized"
    GET_STRUCTURE = "get_structure"


class AnchorMode(str, Enum):
    """How a structural op identifies its target.

    Positional indexes are intentionally rejected for structural ops because
    they shift on insert/delete. Heading text is the only allowed anchor in
    slice 1.
    """

    HEADING_TEXT = "heading_text"


class WordRuntimeError(Exception):
    """Base for taxonomy-classified runtime failures."""


class BackendUnavailable(WordRuntimeError):
    """Backend cannot be constructed (e.g. pywin32 missing, Word not installed)."""


class FileLockedByOther(WordRuntimeError):
    """Document is open in another process (likely the user's own Word)."""


class UnknownAnchor(WordRuntimeError):
    """No paragraph matched the requested anchor."""


class NotConnected(WordRuntimeError):
    """Backend method called before connect() / after teardown."""


@dataclass
class HeadingInfo:
    text: str
    level: int
    paragraph_index: int


@dataclass
class WordHeading(HeadingInfo):
    """Backwards-friendly alias used by the verifier; identical to HeadingInfo."""


@dataclass
class WordStructure:
    """Compact structural snapshot used by the verifier and result payload."""

    headings: list[HeadingInfo] = field(default_factory=list)
    toc_entries: list[dict[str, Any]] = field(default_factory=list)
    has_toc_field: bool = False
    field_codes: list[str] = field(default_factory=list)
    has_track_changes: bool = False
    revision_count: int = 0
    page_count: int | None = None
    paragraph_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "headings": [
                {"text": h.text, "level": h.level, "paragraph_index": h.paragraph_index}
                for h in self.headings
            ],
            "toc_entries": list(self.toc_entries),
            "has_toc_field": self.has_toc_field,
            "field_codes": list(self.field_codes),
            "has_track_changes": self.has_track_changes,
            "revision_count": self.revision_count,
            "page_count": self.page_count,
            "paragraph_count": self.paragraph_count,
        }


@dataclass
class WordRuntimeOp:
    """A single structural op emitted by the agent tool."""

    op: OpKind
    anchor_mode: AnchorMode = AnchorMode.HEADING_TEXT
    anchor: str | None = None
    new_text: str | None = None
    style: str | None = None
    level: int | None = None
    levels: str | None = None
    title: str | None = None

    def validate_for_anchor(self) -> None:
        """Raise if anchor is required but missing or unsupported."""
        anchor_required = {
            OpKind.REPLACE_IN_HEADING,
            OpKind.INSERT_PARAGRAPH_AFTER_HEADING,
            OpKind.SET_HEADING_TEXT,
        }
        if self.op in anchor_required:
            if self.anchor_mode != AnchorMode.HEADING_TEXT:
                raise UnknownAnchor(
                    f"op {self.op.value} requires anchor_mode=heading_text "
                    f"(got {self.anchor_mode.value})"
                )
            if not (self.anchor and self.anchor.strip()):
                raise UnknownAnchor(
                    f"op {self.op.value} requires a non-empty anchor heading text"
                )


@dataclass
class WordRuntimeRequest:
    path: Path
    ops: list[WordRuntimeOp]
    conversation_id: str | None = None
    save: bool = True
    refresh_fields_on_save: bool = True
    backup_dir: Path | None = None
    keep_backups: int = 3


@dataclass
class WordRuntimeResult:
    path: str
    backup_path: str | None
    ops_applied: int
    structure_before: dict[str, Any]
    structure_after: dict[str, Any]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "word_runtime_edit",
            "path": self.path,
            "backup_path": self.backup_path,
            "ops_applied": self.ops_applied,
            "structure_before": self.structure_before,
            "structure_after": self.structure_after,
            "notes": list(self.notes),
        }
