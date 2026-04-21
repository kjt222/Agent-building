"""Knowledge Manager for hybrid retrieval strategy.

Implements the ChatGPT/Claude Projects approach:
- Small KB → Context Packing (direct injection)
- Large KB → RAG retrieval (FTS5 + optional vector search via RRF)

The decision is based on total token count vs context window threshold.
"""

from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .database import Database, get_database

if TYPE_CHECKING:
    from ..rag.service import RagService
    from ..rag.store import SqliteVecStore
    from ..models.base import ModelAdapter

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Estimate token count for text.

    Simple heuristic: ~4 characters per token for English,
    ~2 characters per token for Chinese.
    This is a rough estimate; actual tokenization varies by model.
    """
    # Count Chinese characters
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    # Estimate: Chinese ~2 chars/token, English ~4 chars/token
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 2 + other_chars / 4)


def file_hash(path: Path) -> str:
    """Calculate MD5 hash of file content."""
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def rrf_merge(
    fts_results: list[dict],
    vec_results: list,
    k: int = 60,
) -> list[dict]:
    """Reciprocal Rank Fusion of FTS5 and vector search results.

    Each result is scored by: score = sum(1 / (k + rank_i))
    Results are keyed by (file_id, chunk_text_prefix) to deduplicate.

    Args:
        fts_results: List of dicts from FTS5 (must have 'id', 'filename', 'kb_name', 'snippet').
        vec_results: List of VecSearchResult from vector search.
        k: RRF constant (default 60, standard value).

    Returns:
        Merged list of dicts sorted by RRF score descending.
        Each dict has: file_id, filename, kb_name, snippet/chunk_text, rrf_score, source.
    """
    scores: dict[str, dict] = {}

    for rank, r in enumerate(fts_results):
        key = f"fts:{r['id']}:{r.get('filename', '')}"
        entry = scores.setdefault(key, {
            "file_id": r["id"],
            "filename": r.get("filename", ""),
            "kb_name": r.get("kb_name", ""),
            "snippet": r.get("snippet", ""),
            "chunk_text": "",
            "rrf_score": 0.0,
            "source": "fts",
        })
        entry["rrf_score"] += 1.0 / (k + rank + 1)

    for rank, r in enumerate(vec_results):
        key = f"vec:{r.file_id}:{r.chunk_text[:80]}"
        entry = scores.setdefault(key, {
            "file_id": r.file_id,
            "filename": r.filename,
            "kb_name": r.kb_name,
            "snippet": "",
            "chunk_text": r.chunk_text,
            "rrf_score": 0.0,
            "source": "vec",
        })
        entry["rrf_score"] += 1.0 / (k + rank + 1)
        if not entry["chunk_text"]:
            entry["chunk_text"] = r.chunk_text
        if entry["source"] == "fts":
            entry["source"] = "both"

    merged = sorted(scores.values(), key=lambda x: x["rrf_score"], reverse=True)
    return merged


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def mmr_rerank(
    candidates: list[dict],
    query_vec: list[float],
    embeddings_map: dict[str, list[float]],
    lambda_: float = 0.7,
    top_k: int = 10,
) -> list[dict]:
    """Maximal Marginal Relevance re-ranking to reduce redundancy.

    Args:
        candidates: Merged results from rrf_merge (must have 'chunk_text' or 'snippet').
        query_vec: Query embedding vector.
        embeddings_map: Mapping of candidate key → embedding vector.
            Key is chunk_text[:80] or filename.
        lambda_: Balance between relevance and diversity (1.0 = pure relevance).
        top_k: Number of results to return.

    Returns:
        Re-ranked list of candidates.
    """
    if not candidates or not query_vec:
        return candidates[:top_k]

    def _get_key(c: dict) -> str:
        return (c.get("chunk_text") or c.get("snippet", ""))[:80]

    def _get_emb(c: dict) -> Optional[list[float]]:
        return embeddings_map.get(_get_key(c))

    selected: list[dict] = []
    remaining = list(candidates)

    for _ in range(min(top_k, len(candidates))):
        best_score = -float("inf")
        best_idx = 0

        for i, cand in enumerate(remaining):
            emb = _get_emb(cand)
            if emb is None:
                # No embedding available, use RRF score as relevance proxy
                relevance = cand.get("rrf_score", 0.0)
            else:
                relevance = _cosine_sim(query_vec, emb)

            # Max similarity to already selected
            max_sim = 0.0
            for sel in selected:
                sel_emb = _get_emb(sel)
                if emb is not None and sel_emb is not None:
                    sim = _cosine_sim(emb, sel_emb)
                    max_sim = max(max_sim, sim)

            mmr_score = lambda_ * relevance - (1 - lambda_) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected.append(remaining.pop(best_idx))

    return selected


class KnowledgeManager:
    """Hybrid knowledge retrieval manager.

    Automatically chooses between:
    - Context Packing: Direct injection for small KBs
    - Hybrid (FTS5+Vec): RRF-merged retrieval for large KBs
    - FTS5 only: Fallback when no embedder or sqlite-vec

    Threshold: 80% of context window (default 128k tokens → ~102k threshold)
    """

    def __init__(
        self,
        db: Optional[Database] = None,
        context_window: int = 128000,
        threshold_ratio: float = 0.8,
        rag_service: Optional["RagService"] = None,
        vec_store: Optional["SqliteVecStore"] = None,
        embedder: Optional["ModelAdapter"] = None,
    ):
        self.db = db or get_database()
        self.context_window = context_window
        self.threshold_ratio = threshold_ratio
        self.threshold = int(context_window * threshold_ratio)
        self.rag_service = rag_service
        self.vec_store = vec_store
        self.embedder = embedder

    def should_use_rag(self, kb_names: list[str]) -> bool:
        """Check if RAG should be used for the given knowledge bases.

        Returns True if total tokens exceed threshold.
        """
        total_tokens = 0
        for kb_name in kb_names:
            stats = self.db.get_kb_stats(kb_name)
            total_tokens += stats.get("total_tokens", 0)

        return total_tokens > self.threshold

    def get_context(
        self,
        query: str,
        kb_names: list[str],
        max_tokens: Optional[int] = None
    ) -> str:
        """Get knowledge context for a query.

        Automatically chooses between Context Packing and RAG.

        Args:
            query: User's query
            kb_names: List of knowledge base names to search
            max_tokens: Optional token limit for context

        Returns:
            Context string to inject into prompt
        """
        if max_tokens is None:
            max_tokens = self.threshold

        if self.should_use_rag(kb_names):
            return self._rag_retrieve(query, kb_names, max_tokens)
        else:
            return self._context_packing(kb_names, max_tokens)

    def _context_packing(self, kb_names: list[str], max_tokens: int) -> str:
        """Context Packing: Load all content directly.

        Used when total KB size < threshold.
        """
        parts = []
        total_tokens = 0

        for kb_name in kb_names:
            content = self.db.get_all_kb_content(kb_name)
            if not content:
                continue

            content_tokens = estimate_tokens(content)

            # Check if we'll exceed limit
            if total_tokens + content_tokens > max_tokens:
                # Truncate if needed
                available_tokens = max_tokens - total_tokens
                if available_tokens > 100:  # Only include if meaningful
                    # Rough truncation
                    truncate_chars = available_tokens * 3  # ~3 chars per token
                    content = content[:truncate_chars] + "\n...(truncated)"
                    parts.append(f"## 知识库: {kb_name}\n\n{content}")
                break

            parts.append(f"## 知识库: {kb_name}\n\n{content}")
            total_tokens += content_tokens

        if not parts:
            return ""

        return "# 相关资料\n\n" + "\n\n---\n\n".join(parts)

    @property
    def _has_hybrid(self) -> bool:
        """Check if hybrid search (FTS5 + Vec) is available."""
        return self.vec_store is not None and self.embedder is not None

    def retrieval_strategy(self, kb_names: list[str]) -> str:
        """Return the strategy that will be used for the given KBs."""
        if not self.should_use_rag(kb_names):
            return "Context Packing"
        if self._has_hybrid:
            return "Hybrid (FTS5+Vec)"
        return "FTS5"

    def hybrid_search(
        self,
        query: str,
        kb_names: list[str],
        top_k: int = 10,
    ) -> list[dict]:
        """Hybrid search: FTS5 + vector search merged via RRF + MMR.

        Returns a list of dicts with file_id, filename, kb_name, chunk_text, snippet, rrf_score.
        """
        # 1. FTS5 results
        fts_results = []
        for kb_name in kb_names:
            fts_results.extend(self.db.search_files(query, kb_name=kb_name, limit=top_k))

        # 2. Vector search results
        vec_results = []
        query_vec = None
        if self._has_hybrid:
            try:
                query_vec = self.embedder.embed(query)
                vec_results = self.vec_store.vector_search(query_vec, top_k=top_k)
                # Filter by kb_names
                kb_set = set(kb_names)
                vec_results = [r for r in vec_results if r.kb_name in kb_set]
            except Exception as e:
                logger.warning("Vector search failed, falling back to FTS5 only: %s", e)

        # 3. RRF merge
        merged = rrf_merge(fts_results, vec_results)

        # 4. MMR rerank to reduce redundancy
        if query_vec and len(merged) > 1:
            # Build embeddings map: embed each candidate's text for MMR comparison.
            # Vec results already have known embeddings (re-embed from text is cheap
            # since we only do it for the merged top candidates, not all chunks).
            embeddings_map: dict[str, list[float]] = {}
            for item in merged[:top_k * 2]:  # Only embed reasonable number
                text = (item.get("chunk_text") or item.get("snippet", ""))[:80]
                if text and text not in embeddings_map:
                    try:
                        embeddings_map[text] = self.embedder.embed(
                            item.get("chunk_text") or item.get("snippet", "")
                        )
                    except Exception:
                        pass  # Skip items we can't embed
            if embeddings_map:
                return mmr_rerank(merged, query_vec, embeddings_map, top_k=top_k)

        return merged[:top_k]

    def _rag_retrieve(
        self,
        query: str,
        kb_names: list[str],
        max_tokens: int
    ) -> str:
        """RAG Retrieve: Use hybrid or FTS5 search for relevant chunks.

        Used when total KB size > threshold.
        """
        # Use hybrid search if available
        if self._has_hybrid:
            merged = self.hybrid_search(query, kb_names)
            if merged:
                return self._format_hybrid_results(merged, max_tokens)

        # Pure FTS5 path
        results = []
        for kb_name in kb_names:
            fts_results = self.db.search_files(query, kb_name=kb_name, limit=10)
            results.extend(fts_results)

        if not results:
            # Fallback to RAG service if available and FTS found nothing
            if self.rag_service:
                return self._rag_service_retrieve(query, kb_names, max_tokens)
            return ""

        # Format FTS results
        parts = []
        total_tokens = 0

        for result in results:
            content = self.db.get_file_content(result["id"])
            if not content:
                continue

            content_tokens = result.get("token_count", estimate_tokens(content))

            if total_tokens + content_tokens > max_tokens:
                snippet = result.get("snippet", content[:500])
                parts.append(
                    f"### {result['filename']} (来自 {result['kb_name']})\n\n{snippet}"
                )
                total_tokens += estimate_tokens(snippet)
                if total_tokens > max_tokens:
                    break
            else:
                parts.append(
                    f"### {result['filename']} (来自 {result['kb_name']})\n\n{content}"
                )
                total_tokens += content_tokens

        if not parts:
            return ""

        return "# 检索到的相关内容\n\n" + "\n\n---\n\n".join(parts)

    def _format_hybrid_results(self, merged: list[dict], max_tokens: int) -> str:
        """Format hybrid search results into context string."""
        parts = []
        total_tokens = 0

        for item in merged:
            # Prefer chunk_text (from vector), fallback to full file content
            text = item.get("chunk_text") or ""
            if not text:
                content = self.db.get_file_content(item["file_id"])
                text = content if content else item.get("snippet", "")

            if not text:
                continue

            text_tokens = estimate_tokens(text)
            if total_tokens + text_tokens > max_tokens:
                # Try snippet
                snippet = (item.get("snippet") or text[:500])
                parts.append(
                    f"### {item['filename']} (来自 {item['kb_name']})\n\n{snippet}"
                )
                total_tokens += estimate_tokens(snippet)
                if total_tokens > max_tokens:
                    break
            else:
                source_tag = f" [{item.get('source', '')}]" if item.get("source") else ""
                parts.append(
                    f"### {item['filename']} (来自 {item['kb_name']}){source_tag}\n\n{text}"
                )
                total_tokens += text_tokens

        if not parts:
            return ""

        return "# 检索到的相关内容\n\n" + "\n\n---\n\n".join(parts)

    def _rag_service_retrieve(
        self,
        query: str,
        kb_names: list[str],
        max_tokens: int
    ) -> str:
        """Use RAG service for vector-based retrieval."""
        if not self.rag_service:
            return ""

        # Query RAG service
        results = self.rag_service.query(query, top_k=10)
        if not results:
            return ""

        parts = []
        total_tokens = 0

        for result in results:
            text = result.text
            text_tokens = estimate_tokens(text)

            if total_tokens + text_tokens > max_tokens:
                break

            source = result.metadata.get("source", "unknown")
            parts.append(f"### 来源: {source}\n\n{text}")
            total_tokens += text_tokens

        if not parts:
            return ""

        return "# 检索到的相关内容\n\n" + "\n\n---\n\n".join(parts)

    def _embed_file(self, file_id: int, content: str, filename: str) -> None:
        """Chunk and embed a file's content into the vector store.

        Args:
            file_id: Database file ID (from file_index).
            content: Full text content.
            filename: For logging.
        """
        if not self._has_hybrid:
            return

        from ..rag.chunker import split_text

        # Default chunk params
        chunk_size = 500
        chunk_overlap = 50
        chunks = split_text(content, chunk_size, chunk_overlap)
        if not chunks:
            return

        # Embed all chunks
        embeddings = []
        for chunk in chunks:
            try:
                emb = self.embedder.embed(chunk)
                embeddings.append(emb)
            except Exception as e:
                logger.warning("Embedding failed for chunk in %s: %s", filename, e)
                return

        # Delete old chunks then insert new
        self.vec_store.delete_by_file(file_id)
        self.vec_store.add_chunks(file_id, chunks, embeddings)
        logger.info("Embedded %d chunks for %s", len(chunks), filename)

    def index_file(
        self,
        kb_name: str,
        file_path: Path,
        content: str,
    ) -> int:
        """Index a file for the knowledge base.

        Args:
            kb_name: Knowledge base name
            file_path: Path to the file
            content: Extracted text content

        Returns:
            File ID in database (0 if no update needed)
        """
        content_hash = hashlib.md5(content.encode()).hexdigest()

        # Check if needs update
        if not self.db.file_needs_update(str(file_path), content_hash):
            logger.debug("Skipped embedding for %s (unchanged)", file_path.name)
            return 0  # No update needed

        token_count = estimate_tokens(content)

        file_id = self.db.index_file(
            kb_name=kb_name,
            path=str(file_path),
            filename=file_path.name,
            extension=file_path.suffix,
            size_bytes=file_path.stat().st_size if file_path.exists() else len(content),
            token_count=token_count,
            content_hash=content_hash,
            content=content,
        )

        # Incremental embedding: only embed changed/new files
        if file_id and self._has_hybrid:
            self._embed_file(file_id, content, file_path.name)

        return file_id

    def index_directory(
        self,
        kb_name: str,
        directory: Path,
        extensions: Optional[list[str]] = None,
    ) -> dict:
        """Index all files in a directory.

        Args:
            kb_name: Knowledge base name
            directory: Directory to index
            extensions: File extensions to include (default: common text formats)

        Returns:
            Statistics dict with indexed/skipped/error/embedded counts
        """
        if extensions is None:
            extensions = [".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml"]

        from ..rag.parsers import extract_text

        stats = {"indexed": 0, "skipped": 0, "embedded": 0, "errors": []}

        for ext in extensions:
            for file_path in directory.rglob(f"*{ext}"):
                try:
                    content = extract_text(file_path)
                    if not content:
                        stats["skipped"] += 1
                        continue

                    result = self.index_file(kb_name, file_path, content)
                    if result:
                        stats["indexed"] += 1
                        if self._has_hybrid:
                            stats["embedded"] += 1
                    else:
                        stats["skipped"] += 1

                except Exception as e:
                    stats["errors"].append(f"{file_path}: {e}")

        return stats

    def get_kb_info(self, kb_name: str) -> dict:
        """Get information about a knowledge base."""
        stats = self.db.get_kb_stats(kb_name)
        files = self.db.get_kb_files(kb_name)

        return {
            "name": kb_name,
            "file_count": stats["file_count"],
            "total_bytes": stats["total_bytes"],
            "total_tokens": stats["total_tokens"],
            "should_use_rag": stats["total_tokens"] > self.threshold,
            "threshold": self.threshold,
            "files": files,
        }

    def search(
        self,
        query: str,
        kb_names: Optional[list[str]] = None,
        limit: int = 10
    ) -> list[dict]:
        """Search across knowledge bases using FTS5.

        Args:
            query: Search query
            kb_names: Knowledge bases to search (None = all)
            limit: Maximum results

        Returns:
            List of search results with snippets
        """
        if kb_names and len(kb_names) == 1:
            return self.db.search_files(query, kb_name=kb_names[0], limit=limit)
        else:
            # Search all
            results = self.db.search_files(query, limit=limit)
            if kb_names:
                # Filter by kb_names
                results = [r for r in results if r["kb_name"] in kb_names]
            return results
