from __future__ import annotations

import json
import logging
import math
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Document:
    doc_id: str
    source_id: str
    text: str
    metadata: dict


@dataclass(frozen=True)
class SearchResult:
    doc_id: str
    text: str
    metadata: dict
    score: float


@dataclass(frozen=True)
class VecSearchResult:
    """Result from sqlite-vec vector search."""
    chunk_id: int
    file_id: int
    chunk_text: str
    distance: float
    filename: str
    kb_name: str


class VectorStore:
    def add_documents(self, documents: list[Document], embeddings: list[list[float]]) -> None:
        raise NotImplementedError

    def delete_by_source(self, source_id: str) -> None:
        raise NotImplementedError

    def delete_source(self, source_id: str) -> None:
        raise NotImplementedError

    def source_needs_update(self, source_id: str, mtime: float) -> bool:
        raise NotImplementedError

    def upsert_source(self, source_id: str, source_path: str, mtime: float) -> None:
        raise NotImplementedError

    def query(
        self, embedding: list[float], top_k: int, score_threshold: float
    ) -> list[SearchResult]:
        raise NotImplementedError


class SqliteVectorStore(VectorStore):
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self._init_db()

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def __enter__(self) -> "SqliteVectorStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_db(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                embedding TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                source_mtime REAL NOT NULL
            )
            """
        )
        self._ensure_source_id_column()
        self.conn.commit()

    def _ensure_source_id_column(self) -> None:
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in cur.fetchall()}
        if "source_id" not in columns:
            cur.execute("ALTER TABLE documents ADD COLUMN source_id TEXT")

    def add_documents(self, documents: list[Document], embeddings: list[list[float]]) -> None:
        if len(documents) != len(embeddings):
            raise ValueError("documents and embeddings length mismatch")
        cur = self.conn.cursor()
        for doc, embedding in zip(documents, embeddings):
            cur.execute(
                "REPLACE INTO documents (doc_id, source_id, text, metadata, embedding) VALUES (?, ?, ?, ?, ?)",
                (
                    doc.doc_id,
                    doc.source_id,
                    doc.text,
                    json.dumps(doc.metadata),
                    json.dumps(embedding),
                ),
            )
        self.conn.commit()

    def delete_by_source(self, source_id: str) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM documents WHERE source_id = ?", (source_id,))
        self.conn.commit()

    def delete_source(self, source_id: str) -> None:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        self.conn.commit()

    def source_needs_update(self, source_id: str, mtime: float) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT source_mtime FROM sources WHERE source_id = ?",
            (source_id,),
        )
        row = cur.fetchone()
        if not row:
            return True
        return float(row[0]) != float(mtime)

    def upsert_source(self, source_id: str, source_path: str, mtime: float) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "REPLACE INTO sources (source_id, source_path, source_mtime) VALUES (?, ?, ?)",
            (source_id, source_path, float(mtime)),
        )
        self.conn.commit()

    def query(
        self, embedding: list[float], top_k: int, score_threshold: float
    ) -> list[SearchResult]:
        cur = self.conn.cursor()
        cur.execute("SELECT doc_id, text, metadata, embedding FROM documents")
        results = []
        for doc_id, text, metadata_json, embedding_json in cur.fetchall():
            doc_embedding = json.loads(embedding_json)
            score = _cosine_similarity(embedding, doc_embedding)
            if score >= score_threshold:
                results.append(
                    SearchResult(
                        doc_id=doc_id,
                        text=text,
                        metadata=json.loads(metadata_json),
                        score=score,
                    )
                )
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]


def _cosine_similarity(vec_a: Iterable[float], vec_b: Iterable[float]) -> float:
    a_list = list(vec_a)
    b_list = list(vec_b)
    if len(a_list) != len(b_list) or not a_list:
        return 0.0
    dot = sum(a * b for a, b in zip(a_list, b_list))
    norm_a = math.sqrt(sum(a * a for a in a_list))
    norm_b = math.sqrt(sum(b * b for b in b_list))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _serialize_float32(vec: list[float]) -> bytes:
    """Serialize a list of floats to little-endian float32 bytes for sqlite-vec."""
    return struct.pack(f"<{len(vec)}f", *vec)


class SqliteVecStore:
    """Vector store backed by sqlite-vec for KNN search.

    Uses the main agent database (file_index table) and adds:
    - vec_meta: tracks embedding model + dimension for change detection
    - chunk_meta: chunk text + file_id reference
    - vec_chunks: sqlite-vec virtual table for KNN search
    """

    @classmethod
    def is_available(cls) -> bool:
        """Check if sqlite-vec extension is available."""
        try:
            import sqlite_vec  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(
        self,
        db_path: Path,
        embedding_model: str,
        embedding_dim: int,
    ) -> None:
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self._load_vec_extension()
        self._init_tables()

    def _load_vec_extension(self) -> None:
        """Load the sqlite-vec extension."""
        import sqlite_vec
        self.conn.enable_load_extension(True)
        sqlite_vec.load(self.conn)
        self.conn.enable_load_extension(False)

    def _init_tables(self) -> None:
        """Create tables and handle model change detection."""
        cur = self.conn.cursor()

        # Metadata table for model/dim tracking
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vec_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Chunk metadata table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunk_meta (
                chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER REFERENCES file_index(id) ON DELETE CASCADE,
                chunk_index INTEGER,
                chunk_text TEXT NOT NULL,
                UNIQUE(file_id, chunk_index)
            )
        """)

        self.conn.commit()

        # Check for model change
        if self._model_changed():
            logger.info(
                "Embedding model changed (now %s dim=%d). Clearing vector tables.",
                self.embedding_model, self.embedding_dim,
            )
            self._clear_and_rebuild()
        else:
            # Ensure vec_chunks table exists with current dim
            self._ensure_vec_table()

        # Write current model info
        self._write_meta("embedding_model", self.embedding_model)
        self._write_meta("embedding_dim", str(self.embedding_dim))

    def _model_changed(self) -> bool:
        """Check if embedding model or dimension has changed."""
        cur = self.conn.cursor()
        cur.execute("SELECT value FROM vec_meta WHERE key = 'embedding_model'")
        row = cur.fetchone()
        stored_model = row[0] if row else None

        cur.execute("SELECT value FROM vec_meta WHERE key = 'embedding_dim'")
        row = cur.fetchone()
        stored_dim = row[0] if row else None

        if stored_model is None and stored_dim is None:
            return False  # First time, no change
        return stored_model != self.embedding_model or stored_dim != str(self.embedding_dim)

    def _clear_and_rebuild(self) -> None:
        """Clear all vector data for a fresh start."""
        cur = self.conn.cursor()
        # Drop the vec table first (virtual table needs DROP)
        cur.execute("DROP TABLE IF EXISTS vec_chunks")
        cur.execute("DELETE FROM chunk_meta")
        self.conn.commit()
        self._ensure_vec_table()

    def _ensure_vec_table(self) -> None:
        """Create the sqlite-vec virtual table if it doesn't exist."""
        cur = self.conn.cursor()
        cur.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                chunk_id INTEGER PRIMARY KEY,
                embedding float[{self.embedding_dim}]
            )
        """)
        self.conn.commit()

    def _write_meta(self, key: str, value: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO vec_meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    def add_chunks(
        self,
        file_id: int,
        chunks: list[str],
        embeddings: list[list[float]],
    ) -> None:
        """Insert chunk text + vectors for a file."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch")
        cur = self.conn.cursor()
        for idx, (text, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute(
                "INSERT OR REPLACE INTO chunk_meta (file_id, chunk_index, chunk_text) VALUES (?, ?, ?)",
                (file_id, idx, text),
            )
            chunk_id = cur.lastrowid
            cur.execute(
                "INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
                (chunk_id, _serialize_float32(emb)),
            )
        self.conn.commit()

    def delete_by_file(self, file_id: int) -> None:
        """Delete all chunks for a file."""
        cur = self.conn.cursor()
        # Get chunk IDs first
        cur.execute("SELECT chunk_id FROM chunk_meta WHERE file_id = ?", (file_id,))
        chunk_ids = [row[0] for row in cur.fetchall()]
        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            cur.execute(f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})", chunk_ids)
        cur.execute("DELETE FROM chunk_meta WHERE file_id = ?", (file_id,))
        self.conn.commit()

    def vector_search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[VecSearchResult]:
        """KNN search using sqlite-vec."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT cm.chunk_id, cm.file_id, cm.chunk_text, v.distance,
                   fi.filename, fi.kb_name
            FROM vec_chunks v
            JOIN chunk_meta cm ON cm.chunk_id = v.chunk_id
            JOIN file_index fi ON fi.id = cm.file_id
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
            """,
            (_serialize_float32(query_embedding), top_k),
        )
        results = []
        for row in cur.fetchall():
            results.append(VecSearchResult(
                chunk_id=row[0],
                file_id=row[1],
                chunk_text=row[2],
                distance=row[3],
                filename=row[4],
                kb_name=row[5],
            ))
        return results

    def has_chunks_for_file(self, file_id: int) -> bool:
        """Check if a file already has chunks indexed."""
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM chunk_meta WHERE file_id = ? LIMIT 1", (file_id,))
        return cur.fetchone() is not None

    def close(self) -> None:
        if self.conn:
            self.conn.close()

    def __enter__(self) -> "SqliteVecStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
