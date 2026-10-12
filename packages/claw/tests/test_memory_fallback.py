"""Memory search must work without optional vector/BM25 dependencies."""
import sys
from unittest.mock import AsyncMock

import pytest
from xdog.claw.core.memory.manager import MemoryManager
from xdog.claw.core.tools.tool_memory import create_memory_tool


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr("xdog.claw.core.memory.indexer._embedding_model_instance", None)
    for name in ('sentence_transformers', 'sqlite_vec', 'rank_bm25'):
        monkeypatch.setitem(sys.modules, name, None)
    ws = tmp_path / 'workspace'
    ws.mkdir()
    (ws / 'MEMORY.md').write_text('xdog-youju hugeman 陆知行\n\nCodex 文档位于仓库路径', encoding='utf-8')
    (ws / 'memory').mkdir()
    (ws / 'memory' / 'daily.md').write_text('日志独特词 dailyneedle', encoding='utf-8')
    (ws / 'conversations').mkdir()
    (ws / 'conversations' / 'archive.md').write_text('归档独特词 archiveneedle', encoding='utf-8')
    return MemoryManager(ws, tmp_path / 'data', 'main')


@pytest.mark.parametrize('query,source', [
    ('xdog-youju', 'MEMORY.md'), ('hugeman', 'MEMORY.md'), ('陆知行', 'MEMORY.md'),
    ('XDOG-YOUJU hugeman', 'MEMORY.md'), ('Codex 仓库 路径', 'MEMORY.md'),
    ('文档，仓库', 'MEMORY.md'), ('日志 dailyneedle', 'daily.md'),
    ('归档 archiveneedle', 'archive.md'),
])
async def test_missing_optional_dependencies(memory, query, source):
    try:
        results = await memory.search(query, top_k=5)
        assert any(chunk.source == source for chunk in results)
        assert all(chunk.text for chunk in results)
    finally:
        await memory._searcher.close()


async def test_refresh_and_no_match(memory):
    try:
        assert await memory.search('totallyabsentneedle') == []
        (memory.workspace_dir / 'memory' / 'new.md').write_text('newneedle 新增日志', encoding='utf-8')
        assert any(c.source == 'new.md' for c in await memory.search('newneedle'))
        (memory.workspace_dir / 'MEMORY.md').write_text('replacedneedle 更新记忆', encoding='utf-8')
        assert any('replacedneedle' in c.text for c in await memory.search('replacedneedle'))
        assert await memory.search('xdog-youju') == []
    finally:
        await memory._searcher.close()


async def test_backend_failure_uses_live_keyword_search(memory, caplog):
    memory._searcher.search = AsyncMock(side_effect=RuntimeError('backend unavailable'))
    results = await memory.search('陆知行')
    assert results and results[0].source == 'MEMORY.md'
    assert 'fallback' in caplog.text.lower()


async def test_tool_fallback_and_failure(memory):
    tool = create_memory_tool()
    ctx = {'workspace_dir': str(memory.workspace_dir)}
    result = await tool.execute('id', {'action': 'search', 'query': '归档 archiveneedle'}, ctx=ctx)
    assert '[archive.md]' in result.content[0].text
    ctx['_memory_search'] = AsyncMock(side_effect=RuntimeError('backend unavailable'))
    result = await tool.execute('id', {'action': 'search', 'query': 'needle'}, ctx=ctx)
    assert 'Error:' in result.content[0].text
    assert 'No results' not in result.content[0].text


def test_mixed_language_tokenization():
    from xdog.claw.core.memory.simple_search import tokenize
    assert tokenize('Codex文档，仓库路径') == ['codex', '文档', '仓库', '库路', '路径']


async def test_bm25_small_corpus_and_incremental_refresh(memory, monkeypatch):
    from types import SimpleNamespace

    from xdog.claw.core.memory.types import MemoryChunk

    class ZeroBM25:
        def __init__(self, corpus):
            assert '文档' in corpus[0]

        def get_scores(self, tokens):
            assert '文档' in tokens
            return [0.0]

    monkeypatch.setitem(sys.modules, 'rank_bm25', SimpleNamespace(BM25Okapi=ZeroBM25))
    hybrid = memory._searcher
    hybrid.index_workspace = AsyncMock()
    hybrid._embedding_model = AsyncMock()
    hybrid._embedding_model.encode_query.side_effect = RuntimeError('embedding unavailable')
    hybrid._indexer.get_all_chunks = AsyncMock(return_value=[MemoryChunk(text='Codex文档', source='MEMORY.md')])
    assert await hybrid.search('文档')
    assert await hybrid.search('文档')
    assert hybrid.index_workspace.await_count == 2


async def test_read_failure_is_not_empty_and_symlinks_stay_confined(memory, tmp_path):
    from xdog.claw.core.memory.simple_search import SimpleMemorySearch
    outside = tmp_path / 'outside.md'
    outside.write_text('outsideneedle', encoding='utf-8')
    (memory.workspace_dir / 'memory' / 'link.md').symlink_to(outside)
    search = SimpleMemorySearch(memory.workspace_dir)
    assert await search.search('outsideneedle') == []
    (memory.workspace_dir / 'MEMORY.md').write_bytes(b'\xff')
    with pytest.raises(UnicodeDecodeError):
        await search.search('needle')
