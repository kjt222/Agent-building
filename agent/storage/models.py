"""Data models for Agent storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Conversation:
    """Conversation record."""
    id: str
    title: str = "New Conversation"
    profile: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    messages: list["Message"] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def preview(self) -> str:
        """Get preview from first user message."""
        for msg in self.messages:
            if msg.role == "user":
                return msg.content[:50]
        return ""


@dataclass
class Message:
    """Message in a conversation."""
    role: str  # 'user', 'assistant', 'system'
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    model: Optional[str] = None
    sources: Optional[list] = None


@dataclass
class UserFact:
    """User fact (biographical information, like ChatGPT's memory).

    Limited to ≤50 facts total, oldest are automatically pruned.
    """
    id: int
    fact: str
    category: str = "general"  # 'preference', 'knowledge', 'context', etc.
    source: Optional[str] = None  # 'explicit', 'inferred', conversation_id
    confidence: float = 1.0
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class FileIndex:
    """File index entry for knowledge base.

    Stores file metadata and content for Context Packing or RAG retrieval.
    """
    id: int
    kb_name: str
    path: str
    filename: str
    extension: str
    size_bytes: int
    token_count: int  # Used for CP vs RAG decision
    content_hash: str  # For change detection
    content: str  # Full text content
    last_indexed: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class KBStats:
    """Knowledge base statistics."""
    kb_name: str
    file_count: int
    total_bytes: int
    total_tokens: int

    @property
    def should_use_rag(self) -> bool:
        """Check if RAG should be used based on token count.

        Uses 80% of 128k context window as threshold.
        """
        threshold = 128000 * 0.8  # ~102k tokens
        return self.total_tokens > threshold


@dataclass
class SearchResult:
    """Search result from FTS5."""
    id: int
    kb_name: str
    path: str
    filename: str
    token_count: int
    snippet: str  # Highlighted snippet
