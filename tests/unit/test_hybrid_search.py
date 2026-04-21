"""Tests for RRF merge and MMR rerank functions."""

import unittest
from dataclasses import dataclass

from agent.storage.knowledge_manager import rrf_merge, mmr_rerank


@dataclass(frozen=True)
class FakeVecResult:
    chunk_id: int
    file_id: int
    chunk_text: str
    distance: float
    filename: str
    kb_name: str


class TestRRFMerge(unittest.TestCase):

    def test_fts_only(self):
        fts = [
            {"id": 1, "filename": "a.txt", "kb_name": "kb1", "snippet": "hello"},
            {"id": 2, "filename": "b.txt", "kb_name": "kb1", "snippet": "world"},
        ]
        merged = rrf_merge(fts, [])
        self.assertEqual(len(merged), 2)
        # First FTS result should have higher score
        self.assertGreater(merged[0]["rrf_score"], merged[1]["rrf_score"])

    def test_vec_only(self):
        vec = [
            FakeVecResult(1, 10, "chunk alpha", 0.1, "a.txt", "kb1"),
            FakeVecResult(2, 20, "chunk beta", 0.2, "b.txt", "kb1"),
        ]
        merged = rrf_merge([], vec)
        self.assertEqual(len(merged), 2)
        self.assertGreater(merged[0]["rrf_score"], merged[1]["rrf_score"])

    def test_both_sources_merged(self):
        fts = [
            {"id": 1, "filename": "a.txt", "kb_name": "kb1", "snippet": "hello"},
        ]
        vec = [
            FakeVecResult(1, 2, "chunk gamma", 0.1, "c.txt", "kb1"),
        ]
        merged = rrf_merge(fts, vec)
        # Should have 2 entries (different files)
        self.assertEqual(len(merged), 2)

    def test_ordering_by_score(self):
        fts = [
            {"id": i, "filename": f"f{i}.txt", "kb_name": "kb1", "snippet": f"s{i}"}
            for i in range(5)
        ]
        vec = [
            FakeVecResult(i, i + 10, f"chunk {i}", float(i) * 0.1, f"v{i}.txt", "kb1")
            for i in range(5)
        ]
        merged = rrf_merge(fts, vec)
        # Verify descending order
        for i in range(len(merged) - 1):
            self.assertGreaterEqual(merged[i]["rrf_score"], merged[i + 1]["rrf_score"])


class TestMMRRerank(unittest.TestCase):

    def test_empty_candidates(self):
        result = mmr_rerank([], [1.0, 0.0], {})
        self.assertEqual(result, [])

    def test_empty_query_vec(self):
        candidates = [{"chunk_text": "hello", "rrf_score": 1.0}]
        result = mmr_rerank(candidates, [], {})
        self.assertEqual(len(result), 1)

    def test_diversity(self):
        """MMR should prefer diversity over pure relevance."""
        candidates = [
            {"chunk_text": "similar A", "snippet": "", "rrf_score": 0.9},
            {"chunk_text": "similar B", "snippet": "", "rrf_score": 0.85},
            {"chunk_text": "different C", "snippet": "", "rrf_score": 0.7},
        ]
        query_vec = [1.0, 0.0]
        embeddings_map = {
            "similar A": [1.0, 0.0],
            "similar B": [0.98, 0.2],  # Very similar to A
            "different C": [0.6, 0.8],  # Moderate relevance, but different direction
        }
        result = mmr_rerank(candidates, query_vec, embeddings_map, lambda_=0.5, top_k=3)
        self.assertEqual(len(result), 3)
        texts = [r["chunk_text"] for r in result]
        # First should be most relevant
        self.assertEqual(texts[0], "similar A")
        # "different C" should come before "similar B" due to diversity penalty on B
        # After selecting A: B penalty ~ sim(B,A) ≈ 0.98, C penalty ~ sim(C,A) ≈ 0.6
        # B mmr = 0.5*sim(B,q) - 0.5*sim(B,A) ≈ 0.5*0.98 - 0.5*0.98 ≈ 0
        # C mmr = 0.5*sim(C,q) - 0.5*sim(C,A) ≈ 0.5*0.6 - 0.5*0.6 ≈ 0
        # Need stronger separation. Use lambda_=0.3 to emphasize diversity more.
        result2 = mmr_rerank(candidates, query_vec, embeddings_map, lambda_=0.3, top_k=3)
        texts2 = [r["chunk_text"] for r in result2]
        self.assertEqual(texts2[0], "similar A")
        # With lambda=0.3, diversity dominates: C is more different from A than B
        self.assertLess(texts2.index("different C"), texts2.index("similar B"))

    def test_top_k_limit(self):
        candidates = [
            {"chunk_text": f"item {i}", "snippet": "", "rrf_score": 1.0 / (i + 1)}
            for i in range(20)
        ]
        result = mmr_rerank(candidates, [1.0], {}, top_k=5)
        self.assertEqual(len(result), 5)


if __name__ == "__main__":
    unittest.main()
