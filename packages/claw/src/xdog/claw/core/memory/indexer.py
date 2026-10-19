"""Memory chunk indexer with real embeddings and sqlite-vec vector search.

Uses ``sentence-transformers`` for local embeddings. Falls back to an
``EmbedFn`` (API-based embedding via the ai provider) when the local
model is unavailable. Uses ``sqlite-vec`` for efficient cosine-distance
vector search inside SQLite.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import threading
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from xdog.agent.async_work import run_owned, wait_owned
from xdog.agent.core import EmbedFn
from xdog.claw.core.memory.types import MemoryChunk

logger = logging.getLogger(__name__)
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Embedding model (lazy singleton)
# ---------------------------------------------------------------------------

_embedding_model_instance: EmbeddingModel | None = None


class EmbeddingModel:
    """Embedding model with local-first, API-fallback strategy.

    Tries ``sentence-transformers`` (local, free, fast). If unavailable,
    falls back to an ``EmbedFn`` (API-based, e.g. from OpenAI or
    Anthropic via the ai provider system).

    The underlying model is loaded lazily on first call to :meth:`encode`.
    """

    DIMENSIONS = 384
    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(
        self,
        cache_dir: Path | None = None,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._embed_fn = embed_fn
        self._model: Any = None
        self._use_api = False
        self._local_lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None or self._use_api:
            return
        try:
            from sentence_transformers import SentenceTransformer

            kwargs: dict[str, Any] = {"device": "cpu"}
            if self._cache_dir is not None:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                kwargs["cache_folder"] = str(self._cache_dir)
            self._model = SentenceTransformer(self.MODEL_NAME, **kwargs)
            logger.info("Loaded local embedding model %s", self.MODEL_NAME)
        except Exception as exc:
            if self._embed_fn is not None:
                logger.info("Local embedding unavailable, using API fallback: %s", exc)
                self._use_api = True
            else:
                logger.warning("Failed to load embedding model and no API fallback: %s", exc)
                raise

    def _encode_local(self, texts: tuple[str, ...]) -> list[list[float]] | None:
        with self._local_lock:
            self._load()
            if self._use_api:
                return None
            embeddings = self._model.encode(list(texts), show_progress_bar=False)
            return [row.tolist() for row in embeddings]

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """Local preparation/inference is owned worker IO; API calls retain their loop."""
        prepared = tuple(texts)
        result = await run_owned(self._encode_local, prepared)
        if result is not None:
            return result
        assert self._embed_fn is not None
        return [await self._embed_fn(text) for text in prepared]

    async def encode_query(self, query: str) -> list[float]:
        """Encode a single query string."""
        result = await self.encode([query])
        return result[0]


def get_embedding_model(
    cache_dir: Path | None = None,
    embed_fn: EmbedFn | None = None,
) -> EmbeddingModel:
    """Return a singleton :class:`EmbeddingModel`."""
    global _embedding_model_instance
    if _embedding_model_instance is None:
        _embedding_model_instance = EmbeddingModel(cache_dir=cache_dir, embed_fn=embed_fn)
    return _embedding_model_instance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _floats_to_blob(vec: list[float]) -> bytes:
    """Pack a float list into a little-endian binary blob for sqlite-vec."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _blob_to_floats(blob: bytes) -> list[float]:
    """Unpack a binary blob back into a list of floats."""
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


@dataclass(frozen=True)
class _FileEvidence:
    text: str
    mtime: float


def _read_file(path: Path) -> _FileEvidence | None:
    try:
        return _FileEvidence(path.read_text(encoding="utf-8"), path.stat().st_mtime)
    except FileNotFoundError:
        return None


def _markdown_files(path: Path) -> tuple[Path, ...]:
    return tuple(sorted(path.glob("*.md")))


async def _close_connection(connection: Any) -> None:
    try:
        await connection.close()
    finally:
        # The worker may be the Connection itself or its owned _thread.
        # Closing the queued connection is not yet proof of physical thread exit.
        worker = getattr(connection, "_thread", connection)
        if getattr(worker, "ident", None) is not None:
            await run_owned(worker.join)


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

class MemoryIndexer:
    """Index markdown files into SQLite with real vector embeddings.

    Uses ``sqlite-vec`` for vector distance queries when available.
    """

    def __init__(
        self,
        db_path: Path,
        embedding_model: EmbeddingModel | None = None,
    ) -> None:
        self.db_path = db_path
        self._embedding_model = embedding_model
        self._conn: Any = None
        self._has_vec: bool = False
        self._db_lock = asyncio.Lock()
        self._closing = False
        self._close_task: asyncio.Task[None] | None = None

    async def _database(self, operation: Callable[[], Coroutine[Any, Any, T]]) -> T:
        if self._closing:
            raise RuntimeError("Memory index is closing")
        async with self._db_lock:
            if self._closing:
                raise RuntimeError("Memory index is closing")
            # All callbacks run on this owner loop; aiosqlite owns the physical
            # connection thread. Cancellation cannot abandon acquisition/commit.
            return await wait_owned(asyncio.create_task(operation()))

    async def _init_db(self) -> None:
        async def initialize() -> None:
            if self._conn is not None:
                return
            import aiosqlite

            await run_owned(lambda: self.db_path.parent.mkdir(parents=True, exist_ok=True))
            connection = aiosqlite.connect(str(self.db_path))
            has_vec = False
            try:
                await connection
                try:
                    import sqlite_vec
                    await connection.enable_load_extension(True)
                    try:
                        # The extension loader must run on the same physical
                        # thread as its SQLite connection, not the Agent loop.
                        await connection.load_extension(sqlite_vec.loadable_path())
                    finally:
                        await connection.enable_load_extension(False)
                    has_vec = True
                except Exception as exc:
                    logger.warning("sqlite-vec not available, vector search disabled: %s", exc)
                await connection.executescript("""
                    CREATE TABLE IF NOT EXISTS chunks (
                        id INTEGER PRIMARY KEY, text TEXT, source TEXT,
                        timestamp REAL, embedding BLOB
                    );
                    CREATE TABLE IF NOT EXISTS indexed_files (
                        path TEXT PRIMARY KEY, last_indexed REAL
                    );
                """)
                if has_vec:
                    try:
                        await connection.execute(f"""
                            CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks
                            USING vec0(embedding float[{EmbeddingModel.DIMENSIONS}])
                        """)
                    except Exception as exc:
                        logger.warning("Failed to create vec_chunks table: %s", exc)
                        has_vec = False
                await connection.commit()
            except BaseException:
                await _close_connection(connection)
                raise
            self._conn, self._has_vec = connection, has_vec

        await self._database(initialize)

    async def _rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[Any]:
        async def read() -> list[Any]:
            async with self._conn.execute(sql, parameters) as cursor:
                return list(await cursor.fetchall())
        return await self._database(read)

    def chunk_markdown(self, text: str) -> list[str]:
        """Split markdown text into chunks of roughly 1600 characters."""
        paragraphs = text.split("\n\n")
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            if current_len + len(p) > 1600 and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_len = len(p)
            else:
                current_chunk.append(p)
                current_len += len(p) + 2
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))
        return chunks

    async def index_file(self, file_path: Path, source_label: str) -> None:
        """Prepare immutable evidence/embeddings, then commit one derived-index update."""
        evidence = await run_owned(_read_file, file_path)
        if evidence is None:
            return
        await self._init_db()
        rows = await self._rows("SELECT last_indexed FROM indexed_files WHERE path = ?", (str(file_path),))
        if rows and rows[0][0] >= evidence.mtime:
            return
        chunks = self.chunk_markdown(evidence.text)
        embeddings = [[0.0] * EmbeddingModel.DIMENSIONS for _ in chunks]
        if chunks and self._embedding_model is not None:
            try:
                embeddings = await self._embedding_model.encode(chunks)
            except Exception as exc:
                logger.warning("Embedding failed, using empty: %s", exc)

        async def commit() -> None:
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                await self._conn.execute("DELETE FROM chunks WHERE source = ?", (source_label,))
                if self._has_vec:
                    await self._conn.execute("""
                        DELETE FROM vec_chunks WHERE rowid NOT IN (SELECT id FROM chunks)
                    """)
                timestamp = time.time()
                for text, embedding in zip(chunks, embeddings):
                    blob = _floats_to_blob(embedding)
                    async with self._conn.execute(
                        "INSERT INTO chunks (text, source, timestamp, embedding) VALUES (?, ?, ?, ?)",
                        (text, source_label, timestamp, blob),
                    ) as cursor:
                        chunk_id = cursor.lastrowid
                    if self._has_vec and chunk_id is not None:
                        try:
                            await self._conn.execute(
                                "INSERT INTO vec_chunks (rowid, embedding) VALUES (?, ?)", (chunk_id, blob))
                        except Exception as exc:
                            logger.warning("vec_chunks insert failed for chunk %s: %s", chunk_id, exc)
                await self._conn.execute(
                    "INSERT OR REPLACE INTO indexed_files (path, last_indexed) VALUES (?, ?)",
                    (str(file_path), evidence.mtime),
                )
                await self._conn.commit()
            except BaseException:
                await self._conn.rollback()
                raise

        await self._database(commit)

    async def index_directory(self, dir_path: Path) -> None:
        """Enumerate detached paths off-loop, preserving the original sorted order."""
        for file_path in await run_owned(_markdown_files, dir_path):
            await self.index_file(file_path, file_path.name)

    async def get_chunks(
        self,
        query_embedding: list[float],
        top_k: int = 10,
    ) -> list[MemoryChunk]:
        """Retrieve the closest chunks by cosine distance.

        Uses sqlite-vec when available, otherwise falls back to scanning
        all rows in Python.
        """
        await self._init_db()

        if self._has_vec:
            return await self._vec_search(query_embedding, top_k)
        return await self._fallback_search(query_embedding, top_k)

    async def _vec_search(
        self, query_embedding: list[float], top_k: int
    ) -> list[MemoryChunk]:
        """Use sqlite-vec virtual table for fast vector search."""
        query_blob = _floats_to_blob(query_embedding)
        sql = """
            SELECT c.text, c.source, c.timestamp, v.distance
            FROM vec_chunks v
            JOIN chunks c ON c.id = v.rowid
            WHERE v.embedding MATCH ?
              AND k = ?
            ORDER BY v.distance
        """
        rows = await self._rows(sql, (query_blob, top_k))

        return [
            MemoryChunk(
                text=row[0],
                source=row[1],
                timestamp=row[2],
                # Convert distance to similarity score (1 - cosine_distance)
                score=max(0.0, 1.0 - row[3]),
            )
            for row in rows
        ]

    async def _fallback_search(
        self, query_embedding: list[float], top_k: int
    ) -> list[MemoryChunk]:
        """Brute-force cosine similarity when sqlite-vec is unavailable."""
        rows = await self._rows("SELECT text, source, timestamp, embedding FROM chunks")

        if not rows:
            return []

        scored: list[tuple[float, Any]] = []
        for row in rows:
            emb = _blob_to_floats(row[3])
            sim = _cosine_similarity(query_embedding, emb)
            scored.append((sim, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            MemoryChunk(
                text=row[0],
                source=row[1],
                timestamp=row[2],
                score=sim,
            )
            for sim, row in scored[:top_k]
        ]

    async def get_all_chunks(self) -> list[MemoryChunk]:
        """Return all indexed chunks (used for BM25 scoring)."""
        await self._init_db()
        rows = await self._rows("SELECT text, source, timestamp FROM chunks")
        return [
            MemoryChunk(text=r[0], source=r[1], timestamp=r[2], score=0.0)
            for r in rows
        ]

    async def reindex(self, workspace_dir: Path) -> None:
        """Rebuild all embeddings (e.g. after model change)."""
        await self._init_db()

        async def clear() -> None:
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                await self._conn.execute("DELETE FROM chunks")
                await self._conn.execute("DELETE FROM indexed_files")
                if self._has_vec:
                    await self._conn.execute("DELETE FROM vec_chunks")
                await self._conn.commit()
            except BaseException:
                await self._conn.rollback()
                raise
        await self._database(clear)
        await self.index_file(workspace_dir / "MEMORY.md", "MEMORY.md")

        # Re-index daily logs
        await self.index_directory(workspace_dir / "memory")

        # Re-index conversation archives
        await self.index_directory(workspace_dir / "conversations")

    async def close(self) -> None:
        """Join admitted connection/transaction work before closing the actual worker."""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._close())
        await wait_owned(self._close_task)

    async def _close(self) -> None:
        async with self._db_lock:
            if self._conn is not None:
                await _close_connection(self._conn)
                self._conn = None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (norm_a * norm_b))
