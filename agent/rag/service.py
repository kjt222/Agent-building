from __future__ import annotations

import hashlib
from collections import OrderedDict
from threading import Lock
from time import perf_counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..models import ModelAdapter
from .chunker import split_text
from .parsers import extract_text
from .store import Document, SearchResult, VectorStore


@dataclass(frozen=True)
class RagConfig:
    db_path: Path
    chunk_size: int
    chunk_overlap: int
    top_k: int
    score_threshold: float
    max_context_chars: int
    extensions: tuple[str, ...]


class RagService:
    def __init__(
        self,
        embedder: ModelAdapter,
        store: VectorStore,
        config: RagConfig,
    ) -> None:
        self.embedder = embedder
        self.store = store
        self.config = config
        self.last_metrics: dict = {}

    def index_path(self, path: Path, force: bool = False) -> int:
        files = list(_iter_files(path, self.config.extensions))
        indexed = 0
        for file_path in files:
            source_id = _hash_path(file_path)
            mtime = file_path.stat().st_mtime
            if not force and not self.store.source_needs_update(source_id, mtime):
                continue
            text = extract_text(file_path)
            chunks = split_text(text, self.config.chunk_size, self.config.chunk_overlap)
            if not chunks:
                continue
            documents = []
            embeddings = []
            for idx, chunk in enumerate(chunks):
                doc_id = f"{source_id}:{idx}"
                metadata = {
                    "source_id": source_id,
                    "source_path": str(file_path),
                    "chunk_index": idx,
                }
                documents.append(Document(doc_id=doc_id, source_id=source_id, text=chunk, metadata=metadata))
                embeddings.append(self.embedder.embed(chunk))
            self.store.delete_by_source(source_id)
            self.store.add_documents(documents, embeddings)
            self.store.upsert_source(source_id, str(file_path), mtime)
            indexed += 1
        return indexed

    def query(self, text: str, top_k: Optional[int] = None) -> list[SearchResult]:
        embedding, cache_hit, embed_ms = _embed_with_cache(self.embedder, text)
        search_start = perf_counter()
        results = self.store.query(
            embedding=embedding,
            top_k=top_k or self.config.top_k,
            score_threshold=self.config.score_threshold,
        )
        search_ms = (perf_counter() - search_start) * 1000.0
        self.last_metrics = {
            "embed_ms": embed_ms,
            "search_ms": search_ms,
            "embed_cache_hit": cache_hit,
        }
        return results

    def remove_path(self, path: Path) -> None:
        source_id = _hash_path(path)
        self.store.delete_by_source(source_id)
        self.store.delete_source(source_id)


def _hash_path(path: Path) -> str:
    digest = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()
    return digest


_QUERY_EMBED_CACHE_MAX = 128
_QUERY_EMBED_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()
_QUERY_EMBED_CACHE_LOCK = Lock()


def _embed_with_cache(embedder: ModelAdapter, text: str) -> tuple[list[float], bool, float]:
    provider = getattr(embedder, "provider", embedder.__class__.__name__)
    model = getattr(embedder, "model", "default")
    key = f"{provider}:{model}:{text}"
    with _QUERY_EMBED_CACHE_LOCK:
        cached = _QUERY_EMBED_CACHE.get(key)
        if cached is not None:
            _QUERY_EMBED_CACHE.move_to_end(key)
            return cached, True, 0.0
    start = perf_counter()
    embedding = embedder.embed(text)
    embed_ms = (perf_counter() - start) * 1000.0
    with _QUERY_EMBED_CACHE_LOCK:
        _QUERY_EMBED_CACHE[key] = embedding
        _QUERY_EMBED_CACHE.move_to_end(key)
        while len(_QUERY_EMBED_CACHE) > _QUERY_EMBED_CACHE_MAX:
            _QUERY_EMBED_CACHE.popitem(last=False)
    return embedding, False, embed_ms


def _iter_files(path: Path, extensions: Iterable[str]) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() in extensions:
            yield path
        return
    for file_path in path.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in extensions:
            yield file_path
