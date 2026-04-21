import tempfile
import unittest
from pathlib import Path

from agent.rag.store import Document, SqliteVectorStore


class TestSqliteVectorStore(unittest.TestCase):
    def test_add_and_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "rag.sqlite"
            store = SqliteVectorStore(db_path)

            docs = [
                Document(doc_id="d1", source_id="s1", text="alpha alpha", metadata={}),
                Document(doc_id="d2", source_id="s2", text="beta beta", metadata={}),
            ]
            embeddings = [[1.0, 0.0], [0.0, 1.0]]
            store.add_documents(docs, embeddings)

            results = store.query([1.0, 0.0], top_k=1, score_threshold=0.0)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].doc_id, "d1")
            store.close()
