import tempfile
import unittest
from pathlib import Path

from agent.rag.service import RagConfig, RagService
from agent.rag.store import SqliteVectorStore


class DummyEmbedder:
    def embed(self, text: str) -> list[float]:
        return [float(text.count("alpha")), float(text.count("beta"))]


class TestRagService(unittest.TestCase):
    def test_index_and_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.txt").write_text("alpha alpha", encoding="utf-8")
            (root / "b.txt").write_text("beta beta", encoding="utf-8")

            db_path = root / "rag.sqlite"
            config = RagConfig(
                db_path=db_path,
                chunk_size=200,
                chunk_overlap=0,
                top_k=2,
                score_threshold=0.0,
                max_context_chars=2000,
                extensions=(".txt",),
            )
            store = SqliteVectorStore(db_path)
            service = RagService(embedder=DummyEmbedder(), store=store, config=config)

            indexed = service.index_path(root)
            self.assertEqual(indexed, 2)

            results = service.query("alpha")
            self.assertTrue(results)
            self.assertEqual(results[0].metadata.get("source_path"), str(root / "a.txt"))
            store.close()
