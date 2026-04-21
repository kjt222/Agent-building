"""Unified SQLite database for Agent system.

This module provides a single SQLite database for all agent data:
- Conversations and messages
- User facts (≤50 biographical facts)
- File index for knowledge bases (with token counts for CP/RAG decision)
- FTS5 full-text search
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Iterator, Any

# Singleton database instance
_db_instance: Optional["Database"] = None


def get_database(db_path: Optional[Path] = None) -> "Database":
    """Get or create the singleton database instance."""
    global _db_instance
    if _db_instance is None:
        if db_path is None:
            # Default path: ~/.agent/agent.db
            db_path = Path.home() / ".agent" / "agent.db"
        _db_instance = Database(db_path)
    return _db_instance


def reset_database():
    """Reset the singleton (for testing)."""
    global _db_instance
    if _db_instance:
        _db_instance.close()
    _db_instance = None


class Database:
    """Unified SQLite database for Agent system."""

    # Schema version for migrations
    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    @property
    def conn(self) -> sqlite3.Connection:
        """Get database connection, creating if needed."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                check_same_thread=False,
                isolation_level=None,  # Autocommit mode
            )
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrent access
            self._conn.execute("PRAGMA journal_mode=WAL")
            # Enable foreign keys
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Cursor]:
        """Context manager for transactions."""
        cursor = self.conn.cursor()
        try:
            cursor.execute("BEGIN")
            yield cursor
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise

    def _init_db(self):
        """Initialize database schema."""
        cur = self.conn.cursor()

        # Schema version table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            )
        """)

        # Check current version
        cur.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        current_version = row[0] if row else 0

        if current_version < self.SCHEMA_VERSION:
            self._create_tables(cur)
            cur.execute("DELETE FROM schema_version")
            cur.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (self.SCHEMA_VERSION,)
            )

    def _create_tables(self, cur: sqlite3.Cursor):
        """Create all tables."""

        # ============================================================
        # Conversations table
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT 'New Conversation',
                profile TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_updated
            ON conversations(updated_at DESC)
        """)

        # ============================================================
        # Messages table
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                sources TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                    ON DELETE CASCADE
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_conversation
            ON messages(conversation_id, created_at)
        """)

        # ============================================================
        # User facts table (≤50 biographical facts, like ChatGPT)
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL UNIQUE,
                category TEXT DEFAULT 'general',
                source TEXT,
                confidence REAL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Trigger to limit facts to 50
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS limit_user_facts
            AFTER INSERT ON user_facts
            BEGIN
                DELETE FROM user_facts
                WHERE id NOT IN (
                    SELECT id FROM user_facts
                    ORDER BY updated_at DESC
                    LIMIT 50
                );
            END
        """)

        # ============================================================
        # File index table (for knowledge bases)
        # ============================================================
        cur.execute("""
            CREATE TABLE IF NOT EXISTS file_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kb_name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                extension TEXT,
                size_bytes INTEGER,
                token_count INTEGER,
                content_hash TEXT,
                content TEXT,
                last_indexed TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_index_kb
            ON file_index(kb_name)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_index_hash
            ON file_index(content_hash)
        """)

        # ============================================================
        # FTS5 full-text search for file content
        # ============================================================
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS file_content_fts USING fts5(
                file_id,
                kb_name,
                filename,
                content,
                tokenize='unicode61'
            )
        """)

        # ============================================================
        # FTS5 full-text search for messages (for searching conversations)
        # ============================================================
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                message_id,
                conversation_id,
                role,
                content,
                tokenize='unicode61'
            )
        """)

    # ================================================================
    # Conversation methods
    # ================================================================

    def create_conversation(
        self,
        conv_id: str,
        title: str = "New Conversation",
        profile: Optional[str] = None
    ) -> str:
        """Create a new conversation."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            INSERT INTO conversations (id, title, profile, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv_id, title, profile, now, now)
        )
        return conv_id

    def get_conversation(self, conv_id: str) -> Optional[dict]:
        """Get conversation by ID."""
        cur = self.conn.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conv_id,)
        )
        row = cur.fetchone()
        if not row:
            return None

        conv = dict(row)
        # Get messages
        messages_cur = self.conn.execute(
            """
            SELECT role, content, model, sources, created_at as timestamp
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at
            """,
            (conv_id,)
        )
        conv["messages"] = []
        for msg_row in messages_cur:
            msg = {
                "role": msg_row["role"],
                "content": msg_row["content"],
                "timestamp": msg_row["timestamp"],
            }
            if msg_row["model"]:
                msg["model"] = msg_row["model"]
            if msg_row["sources"]:
                msg["sources"] = json.loads(msg_row["sources"])
            conv["messages"].append(msg)

        return conv

    def list_conversations(self, limit: int = 100) -> list[dict]:
        """List all conversations with message count > 0."""
        cur = self.conn.execute(
            """
            SELECT
                c.id, c.title, c.profile, c.created_at, c.updated_at,
                COUNT(m.id) as message_count,
                (SELECT content FROM messages
                 WHERE conversation_id = c.id AND role = 'user'
                 ORDER BY created_at LIMIT 1) as preview
            FROM conversations c
            LEFT JOIN messages m ON c.id = m.conversation_id
            GROUP BY c.id
            HAVING message_count > 0
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [dict(row) for row in cur]

    def update_conversation(self, conv_id: str, title: Optional[str] = None):
        """Update conversation metadata."""
        now = datetime.now().isoformat()
        if title:
            self.conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, conv_id)
            )
        else:
            self.conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id)
            )

    def delete_conversation(self, conv_id: str) -> bool:
        """Delete a conversation and all its messages."""
        # Delete from FTS first
        self.conn.execute(
            "DELETE FROM messages_fts WHERE conversation_id = ?",
            (conv_id,)
        )
        # Delete messages (will cascade, but explicit for clarity)
        self.conn.execute(
            "DELETE FROM messages WHERE conversation_id = ?",
            (conv_id,)
        )
        # Delete conversation
        cur = self.conn.execute(
            "DELETE FROM conversations WHERE id = ?",
            (conv_id,)
        )
        return cur.rowcount > 0

    # ================================================================
    # Message methods
    # ================================================================

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
        sources: Optional[list] = None
    ) -> int:
        """Add a message to a conversation."""
        now = datetime.now().isoformat()
        sources_json = json.dumps(sources) if sources else None

        cur = self.conn.execute(
            """
            INSERT INTO messages (conversation_id, role, content, model, sources, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (conv_id, role, content, model, sources_json, now)
        )
        message_id = cur.lastrowid

        # Update FTS index
        self.conn.execute(
            """
            INSERT INTO messages_fts (message_id, conversation_id, role, content)
            VALUES (?, ?, ?, ?)
            """,
            (message_id, conv_id, role, content)
        )

        # Update conversation timestamp and title if needed
        conv = self.get_conversation(conv_id)
        if conv and conv.get("title") == "New Conversation" and role == "user":
            title = content[:30] + ("..." if len(content) > 30 else "")
            self.update_conversation(conv_id, title=title)
        else:
            self.update_conversation(conv_id)

        return message_id

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search in messages."""
        cur = self.conn.execute(
            """
            SELECT
                m.id, m.conversation_id, m.role, m.content, m.created_at,
                c.title as conversation_title
            FROM messages_fts fts
            JOIN messages m ON fts.message_id = m.id
            JOIN conversations c ON m.conversation_id = c.id
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit)
        )
        return [dict(row) for row in cur]

    # ================================================================
    # User facts methods
    # ================================================================

    def add_user_fact(
        self,
        fact: str,
        category: str = "general",
        source: Optional[str] = None,
        confidence: float = 1.0
    ) -> int:
        """Add or update a user fact."""
        now = datetime.now().isoformat()

        # Try to update existing fact
        cur = self.conn.execute(
            """
            UPDATE user_facts
            SET category = ?, source = ?, confidence = ?, updated_at = ?
            WHERE fact = ?
            """,
            (category, source, confidence, now, fact)
        )

        if cur.rowcount == 0:
            # Insert new fact
            cur = self.conn.execute(
                """
                INSERT INTO user_facts (fact, category, source, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (fact, category, source, confidence, now, now)
            )

        return cur.lastrowid or 0

    def get_user_facts(self, category: Optional[str] = None, limit: int = 50) -> list[dict]:
        """Get user facts, optionally filtered by category."""
        if category:
            cur = self.conn.execute(
                """
                SELECT * FROM user_facts
                WHERE category = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (category, limit)
            )
        else:
            cur = self.conn.execute(
                """
                SELECT * FROM user_facts
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,)
            )
        return [dict(row) for row in cur]

    def delete_user_fact(self, fact_id: int) -> bool:
        """Delete a user fact by ID."""
        cur = self.conn.execute(
            "DELETE FROM user_facts WHERE id = ?",
            (fact_id,)
        )
        return cur.rowcount > 0

    # ================================================================
    # File index methods
    # ================================================================

    def index_file(
        self,
        kb_name: str,
        path: str,
        filename: str,
        extension: str,
        size_bytes: int,
        token_count: int,
        content_hash: str,
        content: str
    ) -> int:
        """Index a file for the knowledge base."""
        now = datetime.now().isoformat()

        # Check if file already exists
        cur = self.conn.execute(
            "SELECT id, content_hash FROM file_index WHERE path = ?",
            (path,)
        )
        existing = cur.fetchone()

        if existing:
            if existing["content_hash"] == content_hash:
                # No change, skip
                return existing["id"]

            # Update existing
            self.conn.execute(
                """
                UPDATE file_index
                SET kb_name = ?, filename = ?, extension = ?,
                    size_bytes = ?, token_count = ?, content_hash = ?,
                    content = ?, last_indexed = ?
                WHERE id = ?
                """,
                (kb_name, filename, extension, size_bytes, token_count,
                 content_hash, content, now, existing["id"])
            )
            file_id = existing["id"]

            # Update FTS
            self.conn.execute(
                "DELETE FROM file_content_fts WHERE file_id = ?",
                (str(file_id),)
            )
        else:
            # Insert new
            cur = self.conn.execute(
                """
                INSERT INTO file_index
                (kb_name, path, filename, extension, size_bytes, token_count,
                 content_hash, content, last_indexed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (kb_name, path, filename, extension, size_bytes, token_count,
                 content_hash, content, now)
            )
            file_id = cur.lastrowid

        # Update FTS index
        self.conn.execute(
            """
            INSERT INTO file_content_fts (file_id, kb_name, filename, content)
            VALUES (?, ?, ?, ?)
            """,
            (str(file_id), kb_name, filename, content)
        )

        return file_id

    def get_kb_stats(self, kb_name: Optional[str] = None) -> dict:
        """Get knowledge base statistics."""
        if kb_name:
            cur = self.conn.execute(
                """
                SELECT
                    COUNT(*) as file_count,
                    SUM(size_bytes) as total_bytes,
                    SUM(token_count) as total_tokens
                FROM file_index
                WHERE kb_name = ?
                """,
                (kb_name,)
            )
        else:
            cur = self.conn.execute(
                """
                SELECT
                    COUNT(*) as file_count,
                    SUM(size_bytes) as total_bytes,
                    SUM(token_count) as total_tokens
                FROM file_index
                """
            )
        row = cur.fetchone()
        return {
            "file_count": row["file_count"] or 0,
            "total_bytes": row["total_bytes"] or 0,
            "total_tokens": row["total_tokens"] or 0,
        }

    def get_kb_files(self, kb_name: str) -> list[dict]:
        """Get all files in a knowledge base."""
        cur = self.conn.execute(
            """
            SELECT id, path, filename, extension, size_bytes, token_count,
                   content_hash, last_indexed
            FROM file_index
            WHERE kb_name = ?
            ORDER BY filename
            """,
            (kb_name,)
        )
        return [dict(row) for row in cur]

    def get_file_content(self, file_id: int) -> Optional[str]:
        """Get file content by ID."""
        cur = self.conn.execute(
            "SELECT content FROM file_index WHERE id = ?",
            (file_id,)
        )
        row = cur.fetchone()
        return row["content"] if row else None

    def get_all_kb_content(self, kb_name: str) -> str:
        """Get all content from a knowledge base (for Context Packing)."""
        cur = self.conn.execute(
            """
            SELECT filename, content FROM file_index
            WHERE kb_name = ?
            ORDER BY filename
            """,
            (kb_name,)
        )
        parts = []
        for row in cur:
            parts.append(f"=== {row['filename']} ===\n{row['content']}")
        return "\n\n".join(parts)

    def search_files(self, query: str, kb_name: Optional[str] = None, limit: int = 10) -> list[dict]:
        """Full-text search in file content."""
        if kb_name:
            cur = self.conn.execute(
                """
                SELECT
                    f.id, f.kb_name, f.path, f.filename, f.token_count,
                    snippet(file_content_fts, 3, '<mark>', '</mark>', '...', 64) as snippet
                FROM file_content_fts fts
                JOIN file_index f ON fts.file_id = f.id
                WHERE file_content_fts MATCH ? AND f.kb_name = ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, kb_name, limit)
            )
        else:
            cur = self.conn.execute(
                """
                SELECT
                    f.id, f.kb_name, f.path, f.filename, f.token_count,
                    snippet(file_content_fts, 3, '<mark>', '</mark>', '...', 64) as snippet
                FROM file_content_fts fts
                JOIN file_index f ON fts.file_id = f.id
                WHERE file_content_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit)
            )
        return [dict(row) for row in cur]

    def delete_file(self, path: str) -> bool:
        """Delete a file from the index."""
        # Get file ID first
        cur = self.conn.execute(
            "SELECT id FROM file_index WHERE path = ?",
            (path,)
        )
        row = cur.fetchone()
        if not row:
            return False

        file_id = row["id"]

        # Delete from FTS
        self.conn.execute(
            "DELETE FROM file_content_fts WHERE file_id = ?",
            (str(file_id),)
        )

        # Delete from index
        self.conn.execute(
            "DELETE FROM file_index WHERE id = ?",
            (file_id,)
        )

        return True

    def file_needs_update(self, path: str, content_hash: str) -> bool:
        """Check if a file needs to be re-indexed."""
        cur = self.conn.execute(
            "SELECT content_hash FROM file_index WHERE path = ?",
            (path,)
        )
        row = cur.fetchone()
        if not row:
            return True  # New file
        return row["content_hash"] != content_hash
