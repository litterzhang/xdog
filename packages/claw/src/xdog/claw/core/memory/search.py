"""Hybrid memory search: vector similarity + BM25 keyword matching.

Combines semantic vector search (via :class:`MemoryIndexer`) with BM25
lexical scoring (via ``rank-bm25``), merges results using Reciprocal
Rank Fusion, and applies temporal decay to favour recent entries.
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path

from xdog.claw.core.memory.indexer import EmbeddingModel, MemoryIndexer
from xdog.claw.core.memory.simple_search import SimpleMemorySearch, tokenize
from xdog.claw.core.memory.types import MemoryChunk

logger = logging.getLogger(__name__)


class HybridMemorySearch:
    """Hybrid vector + BM25 search over indexed memory chunks.

    Parameters
    ----------
    workspace_dir
        Root of the workspace (contains ``MEMORY.md``, ``memory/``, ``conversations/``).
    db_path
        Path to the SQLite database for indexed chunks.
    embedding_model
        An :class:`EmbeddingModel` instance for encoding queries.
    """

    def __init__(
        self,
        workspace_dir: Path,
        db_path: Path,
        embedding_model: EmbeddingModel,
    ) -> None:
        self._workspace_dir = workspace_dir
        self._embedding_model = embedding_model
        self._indexer = MemoryIndexer(db_path, embedding_model)
        self._keyword = SimpleMemorySearch(workspace_dir)

    async def search(self, query: str, top_k: int = 10) -> list[MemoryChunk]:
        """Run hybrid search: vector + BM25 + RRF + temporal decay.

        1. Incrementally refresh changed files.
        2. Embed the query and retrieve top-20 by vector similarity.
        3. Run BM25 over all indexed chunks for top-20 keyword matches.
        4. Merge with Reciprocal Rank Fusion.
        5. Apply temporal decay.
        6. Return *top_k* results.
        """
        if not query.strip() or top_k <= 0:
            return []

        await self.index_workspace()

        candidate_k = max(top_k, 20)

        # Vector search
        try:
            query_embedding = await self._embedding_model.encode_query(query)
            vector_results = [
                chunk for chunk in await self._indexer.get_chunks(query_embedding, top_k=candidate_k)
                if chunk.score > 0
            ]
        except Exception as exc:
            logger.warning("Vector search failed: %s", exc)
            vector_results = []

        # BM25 search
        bm25_results = await self._bm25_search(query, top_k=candidate_k)

        # Merge with RRF
        if vector_results and bm25_results:
            merged = reciprocal_rank_fusion(vector_results, bm25_results)
        elif vector_results:
            merged = vector_results
        elif bm25_results:
            merged = bm25_results
        else:
            return []

        # Apply temporal decay
        decayed = temporal_decay(merged)

        return decayed[:top_k]

    async def _bm25_search(
        self, query: str, top_k: int = 20
    ) -> list[MemoryChunk]:
        """Score all indexed chunks with BM25."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            logger.warning("rank-bm25 unavailable; using live keyword fallback")
            return await self._keyword.search(query, top_k=top_k)

        all_chunks = await self._indexer.get_all_chunks()
        if not all_chunks:
            return await self._keyword.search(query, top_k=top_k)

        # Tokenize documents
        corpus = [tokenize(chunk.text) for chunk in all_chunks]
        query_tokens = tokenize(query)

        if not corpus or not query_tokens:
            return []

        bm25 = BM25Okapi(corpus)
        scores = bm25.get_scores(query_tokens)

        # Pair scores with chunks and sort
        scored = sorted(
            zip(scores, all_chunks),
            key=lambda x: x[0],
            reverse=True,
        )

        results = [
            MemoryChunk(
                text=chunk.text,
                source=chunk.source,
                score=float(score),
                timestamp=chunk.timestamp,
            )
            for score, chunk in scored[:top_k]
            if score > 0
        ]
        # Okapi IDF can be zero/negative in small corpora even for exact hits.
        return results or await self._keyword.search(query, top_k=top_k)

    async def index_workspace(self) -> None:
        """Index MEMORY.md, daily logs, and conversation archives."""
        ws = self._workspace_dir

        memory_file = ws / "MEMORY.md"
        if memory_file.exists():
            await self._indexer.index_file(memory_file, "MEMORY.md")

        await self._indexer.index_directory(ws / "memory")
        await self._indexer.index_directory(ws / "conversations")

    async def reindex_file(self, file_path: Path, source_label: str) -> None:
        """Re-index a single file after a write.

        Called by the memory_write tool hook to keep the index fresh.
        """
        await self._indexer.index_file(file_path, source_label)

    async def close(self) -> None:
        """Close the underlying indexer connection."""
        await self._indexer.close()


# ---------------------------------------------------------------------------
# Shared scoring functions (kept as module-level for backward compat)
# ---------------------------------------------------------------------------

def reciprocal_rank_fusion(
    rank1: list[MemoryChunk],
    rank2: list[MemoryChunk],
    k: int = 60,
) -> list[MemoryChunk]:
    """Merge two ranked lists using Reciprocal Rank Fusion.

    Each chunk receives a score of ``1 / (k + rank + 1)`` from each list
    it appears in.  Chunks present in both lists accumulate higher scores.
    """
    scores: dict[str, float] = {}
    chunks_map: dict[str, MemoryChunk] = {}

    for r, chunk in enumerate(rank1):
        key = f"{chunk.source}:{chunk.text}"
        scores[key] = scores.get(key, 0) + 1.0 / (k + r + 1)
        chunks_map[key] = chunk

    for r, chunk in enumerate(rank2):
        key = f"{chunk.source}:{chunk.text}"
        scores[key] = scores.get(key, 0) + 1.0 / (k + r + 1)
        chunks_map[key] = chunk

    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [
        MemoryChunk(
            text=chunks_map[key].text,
            source=chunks_map[key].source,
            score=scores[key],
            timestamp=chunks_map[key].timestamp,
        )
        for key in sorted_keys
    ]


def temporal_decay(
    chunks: list[MemoryChunk],
    decay_rate: float = 0.01,
) -> list[MemoryChunk]:
    """Apply exponential time decay to chunk scores.

    Chunks from ``MEMORY.md`` are exempt (evergreen memory).
    """
    now = time.time()
    result: list[MemoryChunk] = []
    for chunk in chunks:
        if chunk.source == "MEMORY.md":
            result.append(chunk)
            continue
        age_days = max(0, (now - chunk.timestamp) / 86400.0)
        new_score = chunk.score * math.exp(-decay_rate * age_days)
        result.append(
            MemoryChunk(
                text=chunk.text,
                source=chunk.source,
                score=new_score,
                timestamp=chunk.timestamp,
            )
        )
    return sorted(result, key=lambda c: c.score, reverse=True)
