"""Word-specific artifact manifest.

Derived from either ``WordRead``'s ``result["structure"]`` payload or
``WordRuntimeEdit``'s ``result.structure_after`` — both share the same
shape (paragraphs, headings, toc_entries, has_toc_field, field_codes,
revision_count, page_count, paragraph_count).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.core.artifact_context.types import ArtifactKind


@dataclass
class WordArtifactManifest:
    kind: ArtifactKind = ArtifactKind.WORD
    path: str = ""
    page_count: int | None = None
    paragraph_count: int = 0
    headings: list[dict] = field(default_factory=list)
    has_toc: bool = False
    toc_entry_count: int = 0
    toc_cache_fresh: bool = False
    has_track_changes: bool = False
    revision_count: int = 0

    @classmethod
    def from_structure(cls, path: str | Path, structure: dict[str, Any]) -> "WordArtifactManifest":
        headings_raw = structure.get("headings") or []
        headings: list[dict] = []
        for h in headings_raw:
            if isinstance(h, dict) and h.get("text"):
                headings.append(
                    {
                        "text": str(h.get("text") or "").strip(),
                        "level": int(h.get("level") or 0) or None,
                    }
                )

        toc_entries = structure.get("toc_entries") or []
        toc_lines: list[str] = []
        for entry in toc_entries:
            if isinstance(entry, dict):
                line = (entry.get("line") or "").strip()
            else:
                line = str(entry or "").strip()
            if line:
                toc_lines.append(line)

        cache_fresh = _toc_cache_matches_headings(headings, toc_lines)

        return cls(
            path=str(path),
            page_count=structure.get("page_count"),
            paragraph_count=int(structure.get("paragraph_count") or 0),
            headings=headings,
            has_toc=bool(structure.get("has_toc_field")),
            toc_entry_count=len(toc_lines),
            toc_cache_fresh=cache_fresh,
            has_track_changes=bool(structure.get("has_track_changes")),
            revision_count=int(structure.get("revision_count") or 0),
        )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "path": self.path,
            "page_count": self.page_count,
            "paragraph_count": self.paragraph_count,
            "headings": list(self.headings),
            "has_toc": self.has_toc,
            "toc_entry_count": self.toc_entry_count,
            "toc_cache_fresh": self.toc_cache_fresh,
            "has_track_changes": self.has_track_changes,
            "revision_count": self.revision_count,
        }

    def to_compact_text(self) -> str:
        lines: list[str] = []
        lines.append(f'<artifact kind="word" path="{self.path}">')
        page_str = f"{self.page_count}" if self.page_count is not None else "?"
        lines.append(
            f"  size: {page_str} pages, {self.paragraph_count} paragraphs"
        )
        if self.headings:
            preview = " / ".join(
                f"L{h.get('level') or '?'}={h.get('text','')}"
                for h in self.headings[:12]
            )
            more = "" if len(self.headings) <= 12 else f" (+{len(self.headings)-12} more)"
            lines.append(f"  headings ({len(self.headings)}): {preview}{more}")
        else:
            lines.append("  headings: none")
        if self.has_toc:
            fresh = "fresh" if self.toc_cache_fresh else "STALE"
            lines.append(
                f"  toc: present, {self.toc_entry_count} entries, cache={fresh}"
            )
        else:
            lines.append("  toc: absent")
        if self.has_track_changes or self.revision_count:
            lines.append(
                f"  track_changes: on, {self.revision_count} revisions"
            )
        lines.append("</artifact>")
        return "\n".join(lines)


def _toc_cache_matches_headings(headings: list[dict], toc_lines: list[str]) -> bool:
    """Heuristic: TOC cache is fresh iff every cached entry text matches a heading.

    The TOC cache line shape is usually ``"<heading text>\\t<page>"`` so we
    take the part before the tab. We do not require ordering or count match
    because Word may include front-matter (e.g. a "Contents" heading) in the
    TOC itself; we only flag a *stale* TOC, not a structural mismatch.
    """
    if not toc_lines:
        return False
    heading_texts = {h.get("text", "") for h in headings if h.get("text")}
    if not heading_texts:
        return False
    for line in toc_lines:
        head = line.split("\t", 1)[0].strip()
        if not head:
            continue
        if head not in heading_texts:
            return False
    return True
