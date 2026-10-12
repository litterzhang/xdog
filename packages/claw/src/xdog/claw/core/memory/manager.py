"""Memory manager — composes daily log, long-term memory, and search into a single facade."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from xdog.agent.core import EmbedFn
from xdog.claw.core.memory.daily_log import DailyLog
from xdog.claw.core.memory.long_term import LongTermMemory
from xdog.claw.core.memory.simple_search import SimpleMemorySearch
from xdog.claw.core.memory.types import MemoryChunk

# Optional: hybrid search with real embeddings
try:
    from xdog.claw.core.memory.indexer import get_embedding_model
    from xdog.claw.core.memory.search import HybridMemorySearch

    _HAS_HYBRID_SEARCH = True
except ImportError:
    _HAS_HYBRID_SEARCH = False

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages all memory subsystems for a group.

    Composes:
    - ``DailyLog`` — append-only daily markdown files
    - ``LongTermMemory`` — evergreen MEMORY.md
    - Search — hybrid (vector + BM25) or keyword-only fallback

    When ``embed_fn`` is provided, it's used as an API-based fallback
    for embeddings when ``sentence-transformers`` is not installed.
    """

    def __init__(
        self,
        workspace_dir: Path,
        data_dir: Path,
        group_id: str,
        *,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self.workspace_dir = workspace_dir
        self.daily_log = DailyLog(workspace_dir / "memory")
        self.long_term = LongTermMemory(workspace_dir)

        # Search — try hybrid, fall back to keyword-only
        self._searcher: Any = None
        self.reindex_fn: Callable[..., Awaitable[None]] | None = None

        if _HAS_HYBRID_SEARCH:
            try:
                db_path = data_dir / "memory" / group_id / "memory.db"
                embedding_model = get_embedding_model(
                    cache_dir=data_dir / "models",
                    embed_fn=embed_fn,
                )
                hybrid = HybridMemorySearch(workspace_dir, db_path, embedding_model)
                self._searcher = hybrid

                async def _reindex_file(path: Path, label: str) -> None:
                    await hybrid.reindex_file(path, label)

                self.reindex_fn = _reindex_file
                logger.info("Using HybridMemorySearch for group %s", group_id)
            except Exception as exc:
                logger.warning(
                    "HybridMemorySearch init failed, falling back: %s", exc,
                )

        if self._searcher is None:
            self._searcher = SimpleMemorySearch(workspace_dir)
            logger.info("Using SimpleMemorySearch for group %s", group_id)

    async def search(self, query: str, top_k: int = 10) -> list[MemoryChunk]:
        """Search memory, falling back on lazy backend failures as well as init failures."""
        try:
            chunks: list[MemoryChunk] = await self._searcher.search(query, top_k=top_k)
            return chunks
        except Exception:
            logger.warning("Memory backend failed; using live keyword fallback", exc_info=True)
            # Let read failures propagate: unavailable memory is not an empty result.
            return await SimpleMemorySearch(self.workspace_dir).search(query, top_k=top_k)

    async def close(self) -> None:
        """Close the optional search backend, including its non-daemon SQLite worker."""
        close = getattr(self._searcher, "close", None)
        if close is not None:
            await close()
