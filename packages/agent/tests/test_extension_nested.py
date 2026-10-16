"""Only independent bounded basic dialogs remain; no custom parent renderer authority."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions import dialogs
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.mcp.stdio import McpError


async def test_legacy_nested_custom_worker_is_refused_without_publishing_or_borrowing_dialogs(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/nested', root / 'nested')
    runtime = ExtensionRuntime(['nested'], root, tmp_path)
    seen = []
    async def ask(extension, value):
        seen.append(value)
        return None
    runtime.dialog_handler = ask
    try:
        with pytest.raises(McpError):
            await runtime.command('ext-nested-view', '')
        assert not seen and runtime.snapshot()['extensions'][0]['state'] == 'failed'
    finally:
        await runtime.aclose()


async def test_plugin_parent_ids_are_rejected_without_borrowing_another_extensions_question():
    assert not hasattr(dialogs, 'render_owner')
    broker, events = DialogBroker(), []
    broker.publish = events.append
    task = asyncio.create_task(broker.ask('owner', {'id': 'worker', 'kind': 'input', 'title': 'Owned'}))
    await asyncio.sleep(0)
    identifier = events[0]['dialog']['id']
    for parent in (identifier, 'foreign'):
        with pytest.raises(ValueError):
            await broker.ask('other', {'id': 'worker', 'kind': 'confirm', 'title': 'Borrow', 'parent_id': parent})
    assert broker.input_owner(identifier) and not task.done() and len(events) == 1
    broker.close()
    assert await task is None


async def test_basic_question_quota_and_close_settle_all_owned_futures_without_nested_state():
    broker, events = DialogBroker(), []
    broker.publish = events.append
    tasks = [asyncio.create_task(broker.ask(str(index), {'id': 'worker', 'kind': 'confirm', 'title': 'Question'}))
             for index in range(8)]
    await asyncio.sleep(0)
    assert len(events) == 8 and broker.has_pending
    with pytest.raises(ValueError, match='queue is full'):
        await broker.ask('extra', {'id': 'worker', 'kind': 'confirm', 'title': 'Too many'})
    assert len({event['dialog']['id'] for event in events}) == 8
    broker.close()
    assert await asyncio.gather(*tasks) == [False] * 8
    assert not broker.has_pending
