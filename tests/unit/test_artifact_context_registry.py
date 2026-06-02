"""Unit tests for ArtifactRegistry + Word manifest derivation (P11.3.1)."""

from __future__ import annotations

import time
import threading

from agent.core.artifact_context import (
    ArtifactKind,
    ArtifactRegistry,
    WordArtifactManifest,
    conversation_id_from_ctx,
    get_registry,
    register_word_artifact,
    reset_all_registries,
    reset_registry,
    serialize_for_context,
)


_STRUCTURE_THREE_HEADINGS_FRESH_TOC = {
    "headings": [
        {"text": "Chapter 1", "level": 1, "paragraph_index": 1},
        {"text": "Chapter 2", "level": 1, "paragraph_index": 4},
        {"text": "Chapter 3", "level": 1, "paragraph_index": 6},
    ],
    "toc_entries": [
        {"line": "Chapter 1\t1"},
        {"line": "Chapter 2\t1"},
        {"line": "Chapter 3\t1"},
    ],
    "has_toc_field": True,
    "field_codes": ["TOC \\o \"1-3\" \\h \\z \\u"],
    "has_track_changes": False,
    "revision_count": 0,
    "page_count": 7,
    "paragraph_count": 9,
}

_STRUCTURE_STALE_TOC = {
    "headings": [
        {"text": "New Title", "level": 1, "paragraph_index": 1},
        {"text": "Chapter 2", "level": 1, "paragraph_index": 4},
    ],
    "toc_entries": [
        {"line": "Old Title\t1"},
        {"line": "Chapter 2\t1"},
    ],
    "has_toc_field": True,
    "page_count": 3,
    "paragraph_count": 6,
}


def setup_function(_func):
    reset_all_registries()


def test_word_manifest_from_fresh_structure():
    manifest = WordArtifactManifest.from_structure(
        "C:\\docs\\thesis.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC
    )
    assert manifest.kind == ArtifactKind.WORD
    assert manifest.path == "C:\\docs\\thesis.docx"
    assert manifest.page_count == 7
    assert manifest.paragraph_count == 9
    assert len(manifest.headings) == 3
    assert manifest.has_toc is True
    assert manifest.toc_entry_count == 3
    assert manifest.toc_cache_fresh is True
    assert manifest.has_track_changes is False


def test_word_manifest_detects_stale_toc():
    manifest = WordArtifactManifest.from_structure("a.docx", _STRUCTURE_STALE_TOC)
    assert manifest.has_toc is True
    assert manifest.toc_cache_fresh is False  # "Old Title" not in current headings


def test_compact_text_renders_useful_fields():
    manifest = WordArtifactManifest.from_structure("a.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC)
    text = manifest.to_compact_text()
    assert text.startswith('<artifact kind="word" path="a.docx">')
    assert text.endswith("</artifact>")
    assert "7 pages" in text
    assert "Chapter 1" in text
    assert "toc:" in text
    assert "fresh" in text


def test_compact_text_marks_stale_toc():
    manifest = WordArtifactManifest.from_structure("a.docx", _STRUCTURE_STALE_TOC)
    text = manifest.to_compact_text()
    assert "STALE" in text


def test_registry_stores_and_returns_latest():
    reg = ArtifactRegistry()
    m1 = WordArtifactManifest.from_structure("a.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC)
    reg.register(m1)
    rec = reg.get("a.docx")
    assert rec is not None
    assert rec.kind == ArtifactKind.WORD


def test_registry_register_overwrites_previous():
    reg = ArtifactRegistry()
    m_stale = WordArtifactManifest.from_structure("a.docx", _STRUCTURE_STALE_TOC)
    reg.register(m_stale)
    m_fresh = WordArtifactManifest.from_structure(
        "a.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC
    )
    reg.register(m_fresh)
    rec = reg.get("a.docx")
    assert rec is not None
    assert rec.manifest.toc_cache_fresh is True  # post-mutation state, not stale


def test_registry_serialize_sorts_most_recent_first():
    reg = ArtifactRegistry()
    rec_old = reg.register(WordArtifactManifest.from_structure(
        "old.docx", _STRUCTURE_STALE_TOC
    ))
    rec_new = reg.register(WordArtifactManifest.from_structure(
        "new.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC
    ))
    # Force a strict timestamp ordering, avoiding any host clock-resolution
    # flakiness around an inter-call sleep.
    rec_old.last_updated_at = 1.0
    rec_new.last_updated_at = 2.0
    text = reg.serialize_for_context(budget_chars=1000)
    assert text.index("new.docx") < text.index("old.docx")


def test_registry_serialize_truncates_under_budget():
    reg = ArtifactRegistry()
    # Each manifest serializes to ~150 chars; budget 100 forces truncation.
    for i in range(5):
        reg.register(WordArtifactManifest.from_structure(
            f"f{i}.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC
        ))
    text = reg.serialize_for_context(budget_chars=100)
    assert "<truncated" in text


def test_serialize_for_context_returns_empty_when_no_artifacts():
    assert serialize_for_context("conv-empty") == ""


def test_get_registry_is_per_conversation():
    reg_a = get_registry("conv-a")
    reg_b = get_registry("conv-b")
    assert reg_a is not reg_b
    reg_a.register(WordArtifactManifest.from_structure("a.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC))
    assert reg_b.get("a.docx") is None


def test_reset_registry_drops_only_target():
    register_word_artifact("conv-a", "a.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC)
    register_word_artifact("conv-b", "b.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC)
    reset_registry("conv-a")
    assert get_registry("conv-a").get("a.docx") is None
    assert get_registry("conv-b").get("b.docx") is not None


def test_register_word_artifact_convenience_round_trip():
    rec = register_word_artifact("conv-c", "c.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC)
    assert rec.path == "c.docx"
    assert rec.manifest.toc_cache_fresh is True
    text = serialize_for_context("conv-c")
    assert "c.docx" in text


def test_conversation_id_from_ctx_prefers_scratch():
    class _Ctx:
        scratch = {"conversation_id": "from-scratch"}
        config = None

    assert conversation_id_from_ctx(_Ctx()) == "from-scratch"


def test_conversation_id_from_ctx_falls_back_to_config():
    class _Cfg:
        conversation_id = "from-config"

    class _Ctx:
        scratch = {}
        config = _Cfg()

    assert conversation_id_from_ctx(_Ctx()) == "from-config"


def test_conversation_id_from_ctx_defaults_when_missing():
    class _Ctx:
        scratch = {}
        config = None

    assert conversation_id_from_ctx(_Ctx()) == "default"


def test_registry_thread_safe_concurrent_register():
    reg = ArtifactRegistry()

    def _worker(i):
        reg.register(WordArtifactManifest.from_structure(
            f"f{i}.docx", _STRUCTURE_THREE_HEADINGS_FRESH_TOC
        ))

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reg.all_records()) == 20
