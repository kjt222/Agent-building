"""Tests for SqliteVecStore (sqlite-vec backed vector search)."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent.rag.store import SqliteVecStore, VecSearchResult


def _setup_file_index(db_path: Path) -> sqlite3.Connection:
    """Create a minimal file_index table for testing JOIN queries."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
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
    conn.commit()
    return conn


def _insert_file(conn: sqlite3.Connection, kb_name: str, filename: str) -> int:
    cur = conn.execute(
        "INSERT INTO file_index (kb_name, path, filename, extension, size_bytes, token_count, content_hash, content, last_indexed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (kb_name, f"/test/{filename}", filename, ".txt", 100, 50, "abc", "content", "2025-01-01"),
    )
    conn.commit()
    return cur.lastrowid


@unittest.skipUnless(SqliteVecStore.is_available(), "sqlite-vec not installed")
class TestSqliteVecStore(unittest.TestCase):

    def test_is_available(self):
        self.assertTrue(SqliteVecStore.is_available())

    def test_add_and_search(self):
        """Insert 10 vectors, query should return correct top-K."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            helper_conn = _setup_file_index(db_path)
            file_id = _insert_file(helper_conn, "kb1", "doc1.txt")
            helper_conn.close()

            store = SqliteVecStore(db_path, "test-model", 4)

            chunks = [f"chunk {i}" for i in range(10)]
            # Vectors: each has a 1.0 at position i%4, rest 0
            embeddings = []
            for i in range(10):
                vec = [0.0] * 4
                vec[i % 4] = 1.0
                embeddings.append(vec)

            store.add_chunks(file_id, chunks, embeddings)

            # Query with [1, 0, 0, 0] should return chunks 0, 4, 8 as closest
            results = store.vector_search([1.0, 0.0, 0.0, 0.0], top_k=3)
            self.assertEqual(len(results), 3)
            self.assertIsInstance(results[0], VecSearchResult)
            self.assertEqual(results[0].kb_name, "kb1")
            self.assertEqual(results[0].filename, "doc1.txt")
            # All top results should have chunk_index 0, 4, or 8 (the ones with dim-0 = 1.0)
            texts = {r.chunk_text for r in results}
            self.assertTrue(texts.issubset({"chunk 0", "chunk 4", "chunk 8"}))

            store.close()

    def test_delete_by_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            helper_conn = _setup_file_index(db_path)
            fid1 = _insert_file(helper_conn, "kb1", "a.txt")
            fid2 = _insert_file(helper_conn, "kb1", "b.txt")
            helper_conn.close()

            store = SqliteVecStore(db_path, "test-model", 2)

            store.add_chunks(fid1, ["a1", "a2"], [[1.0, 0.0], [0.9, 0.1]])
            store.add_chunks(fid2, ["b1", "b2"], [[0.0, 1.0], [0.1, 0.9]])

            # Delete file 1's chunks
            store.delete_by_file(fid1)

            # Search should only return file 2's chunks
            results = store.vector_search([0.0, 1.0], top_k=10)
            self.assertTrue(all(r.file_id == fid2 for r in results))

            store.close()

    def test_model_change_clears_data(self):
        """Changing embedding model should clear all vector data."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            helper_conn = _setup_file_index(db_path)
            fid = _insert_file(helper_conn, "kb1", "doc.txt")
            helper_conn.close()

            # First model
            store = SqliteVecStore(db_path, "model-v1", 2)
            store.add_chunks(fid, ["hello"], [[1.0, 0.0]])
            self.assertTrue(store.has_chunks_for_file(fid))
            store.close()

            # Different model — should clear
            store2 = SqliteVecStore(db_path, "model-v2", 2)
            self.assertFalse(store2.has_chunks_for_file(fid))
            store2.close()

    def test_dim_change_clears_data(self):
        """Changing embedding dimension should clear all vector data."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            helper_conn = _setup_file_index(db_path)
            fid = _insert_file(helper_conn, "kb1", "doc.txt")
            helper_conn.close()

            store = SqliteVecStore(db_path, "model-v1", 2)
            store.add_chunks(fid, ["hello"], [[1.0, 0.0]])
            store.close()

            # Same model, different dim
            store2 = SqliteVecStore(db_path, "model-v1", 4)
            self.assertFalse(store2.has_chunks_for_file(fid))
            store2.close()

    def test_has_chunks_for_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            helper_conn = _setup_file_index(db_path)
            fid = _insert_file(helper_conn, "kb1", "doc.txt")
            helper_conn.close()

            store = SqliteVecStore(db_path, "test-model", 2)
            self.assertFalse(store.has_chunks_for_file(fid))
            store.add_chunks(fid, ["hi"], [[1.0, 0.0]])
            self.assertTrue(store.has_chunks_for_file(fid))
            store.close()


if __name__ == "__main__":
    unittest.main()
