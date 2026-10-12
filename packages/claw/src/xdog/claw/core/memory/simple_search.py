"""Simple keyword-based memory search.

Scans all ``.md`` files in the workspace memory directory and MEMORY.md
for text matching the query.  No external dependencies required.

When ``sentence-transformers`` and ``sqlite-vec`` are available the full
hybrid vector + BM25 pipeline from :mod:`claw.memory.indexer` and
:mod:`claw.memory.search` should be used instead.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from xdog.claw.core.memory.types import MemoryChunk


def tokenize(text: str) -> list[str]:
    """Split punctuation/Latin words and use overlapping bigrams for unspaced CJK."""
    tokens: list[str] = []
    for part in re.findall(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+(?:[-_][^\W_\u3400-\u9fff]+)*", text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", part) and len(part) > 1:
            tokens.extend(part[i:i + 2] for i in range(len(part) - 1))
        else:
            tokens.append(part)
    return list(dict.fromkeys(tokens))


class SimpleMemorySearch:
    """Keyword search over workspace markdown files.

    Searches MEMORY.md and all daily log files under ``memory/`` for
    lines containing any of the query terms.
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace_dir = workspace_dir

    async def search(self, query: str, top_k: int = 10) -> list[MemoryChunk]:
        """Return up to *top_k* chunks matching *query* keywords."""
        return await asyncio.to_thread(self._search, query, top_k)

    def _search(self, query: str, top_k: int) -> list[MemoryChunk]:
        terms = tokenize(query)
        if not terms or top_k <= 0:
            return []

        results: list[MemoryChunk] = []

        # Search MEMORY.md (evergreen — high base score)
        memory_file = self._workspace_dir / "MEMORY.md"
        if memory_file.exists():
            results.extend(
                self._search_file(memory_file, "MEMORY.md", terms, base_score=2.0)
            )

        # Search daily log files
        memory_dir = self._workspace_dir / "memory"
        if memory_dir.is_dir():
            for md_file in sorted(memory_dir.glob("*.md"), reverse=True):
                results.extend(
                    self._search_file(md_file, md_file.name, terms, base_score=1.0)
                )
                if len(results) >= top_k * 3:
                    break  # enough candidates

        # Search conversation archives
        conv_dir = self._workspace_dir / "conversations"
        if conv_dir.is_dir():
            for md_file in sorted(conv_dir.glob("*.md"), reverse=True):
                results.extend(
                    self._search_file(md_file, md_file.name, terms, base_score=0.5)
                )
                if len(results) >= top_k * 5:
                    break

        # Sort by score descending, return top_k
        results.sort(key=lambda c: c.score, reverse=True)
        return results[:top_k]

    def _search_file(
        self,
        file_path: Path,
        source: str,
        terms: list[str],
        base_score: float,
    ) -> list[MemoryChunk]:
        """Search a single file for paragraphs matching query terms."""
        if not file_path.resolve().is_relative_to(self._workspace_dir.resolve()):
            return []
        text = file_path.read_text(encoding="utf-8")

        ts = file_path.stat().st_mtime

        # Split into paragraphs (~400 token chunks)
        paragraphs = text.split("\n\n")
        chunks: list[MemoryChunk] = []

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            lower_para = para.casefold()
            matched = sum(1 for t in terms if t in lower_para)
            if matched == 0:
                continue

            # Score: base_score * (fraction of terms matched)
            score = base_score * (matched / len(terms))
            chunks.append(MemoryChunk(
                text=para[:400],  # limit chunk size
                source=source,
                score=score,
                timestamp=ts,
            ))

        return chunks
