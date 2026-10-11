from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent.extensions.dialogs import DialogBroker, render_owner
from xdog.agent.extensions.runtime import ExtensionRuntime


def request(kind='resize', **options):
    event = {'kind': kind, 'width': 60, 'height': 5, 'painted': -1, 'generation': 1, **options}
    if kind == 'key':
        event.update(key=options.get('key', 'n'), ctrl=False, alt=False, shift=False)
    return event


async def until(predicate):
    async with asyncio.timeout(6):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.mark.parametrize('mode', ['', 'custom', 'timeout'])
async def test_owned_worker_nested_questions_suspend_frame_once_and_return_to_parent(tmp_path, mode):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['nested'], root, tmp_path)
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    runtime.dialog_handler, runtime.custom_handler = broker.ask, broker.ask_custom
    try:
        task = asyncio.create_task(runtime.command('ext-nested-view', mode))
        await until(lambda: notices)
        parent = notices[0]['dialog']['id']
        assert broker.request_custom(parent, request())
        await until(lambda: any(item['type'] == 'extension_custom_frame' for item in notices))
        assert broker.request_custom(parent, request('key', painted=1))
        await until(lambda: len([item for item in notices if item['type'] == 'extension_dialog']) >= 2)
        nested = [item['dialog'] for item in notices if item['type'] == 'extension_dialog'][-1]
        assert nested['parent_id'] == parent and broker.custom.views[parent].suspended
        assert not broker.request_custom(parent, request('key', painted=1))
        if mode == 'custom':
            child = nested['id']
            assert broker.request_custom(child, request())
            await until(lambda: any(item['type'] == 'extension_custom_frame' and item['id'] == child
                                    for item in notices))
            assert broker.request_custom(child, request('key', painted=1))
            await until(lambda: len([item for item in notices if item['type'] == 'extension_dialog']) >= 3)
            grandchild = [item['dialog'] for item in notices if item['type'] == 'extension_dialog'][-1]
            assert grandchild['parent_id'] == child
            assert broker.resolve(grandchild['id'], 'grand')
            await until(lambda: not broker.contains(grandchild['id']))
            # Resume requires fresh epoch/frame evidence, never the pre-question frame.
            assert broker.request_custom(child, request(generation=2))
            await until(lambda: any(item.get('generation') == 2 and item.get('id') == child for item in notices))
            frame = [item for item in notices if item.get('id') == child
                     and item['type'] == 'extension_custom_frame'][-1]
            assert broker.request_custom(child, request('key', key='enter', generation=2, painted=frame['revision']))
            await until(lambda: not broker.contains(child))
        else:
            if mode != 'timeout':
                assert broker.resolve(nested['id'], 'answer')
            await until(lambda: len([item for item in notices if item['type'] == 'extension_dialog']) >= 3)
            confirm = [item['dialog'] for item in notices if item['type'] == 'extension_dialog'][-1]
            assert confirm['parent_id'] == parent and broker.resolve(confirm['id'], True)
            await until(lambda: not broker.contains(confirm['id']))
        assert broker.request_custom(parent, request(generation=3))
        await until(lambda: any(item.get('id') == parent and item.get('generation') == 3 for item in notices))
        frame = [item for item in notices if item.get('id') == parent and item['type'] == 'extension_custom_frame'][-1]
        assert broker.request_custom(parent, request('key', key='enter', generation=3, painted=frame['revision']))
        value = json.loads(await task)
        assert value['calls'] == 1 and value['values'] == (
            ['grand', {'child': False}] if mode == 'custom' else [None if mode == 'timeout' else 'answer', True])
        assert runtime.snapshot()['extensions'][0]['state'] == 'active' and not broker.custom.views
    finally:
        broker.close()
        await runtime.aclose()


async def test_plugin_parent_ids_are_not_authority_and_context_cannot_borrow_other_extension():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    task = asyncio.create_task(broker.ask('one', {'id': 'worker', 'kind': 'input', 'title': 'Root',
                                                  'parent_id': 'forged'}))
    await asyncio.sleep(0)
    assert 'parent_id' not in notices[0]['dialog']
    token = render_owner.set((broker, notices[0]['dialog']['id']))
    try:
        with pytest.raises(ValueError):
            await broker.ask('other', {'id': 'worker', 'kind': 'input', 'title': 'Foreign'})
    finally:
        render_owner.reset(token)
        broker.close()
    assert await task is None


async def test_nested_depth_bound_and_cascade_close_settle_all_owned_futures():
    broker, notices, tasks = DialogBroker(), [], []
    broker.publish = notices.append
    async def render(value):
        return {'lines': ['view']}
    try:
        parent = ''
        for index in range(4):
            token = render_owner.set((broker, parent)) if parent else None
            try:
                tasks.append(asyncio.create_task(broker.ask_custom('same', {
                    'kind': 'custom', 'id': 'worker', 'view_id': 'view', 'title': str(index)}, render)))
            finally:
                if token is not None:
                    render_owner.reset(token)
            await until(lambda: len(notices) >= index + 1)
            parent = notices[-1]['dialog']['id']
        token = render_owner.set((broker, parent))
        try:
            with pytest.raises(ValueError):
                await broker.ask('same', {'id': 'worker', 'kind': 'input', 'title': 'Too deep'})
        finally:
            render_owner.reset(token)
        assert broker.resolve(notices[0]['dialog']['id'], None)
        assert await asyncio.wait_for(asyncio.gather(*tasks), 3) == [None] * 4
        assert not broker.custom.views and not broker._pending
    finally:
        broker.close()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_canceling_real_callback_with_nested_question_reaps_worker_and_graph(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions', root)
    runtime = ExtensionRuntime(['nested'], root, tmp_path)
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    runtime.dialog_handler, runtime.custom_handler = broker.ask, broker.ask_custom
    try:
        await runtime.initialize()
        process = runtime._manager._clients['nested'].process
        task = asyncio.create_task(runtime.command('ext-nested-view', ''))
        await until(lambda: notices)
        parent = notices[0]['dialog']['id']
        broker.request_custom(parent, request())
        await until(lambda: any(item['type'] == 'extension_custom_frame' for item in notices))
        broker.request_custom(parent, request('key', painted=1))
        await until(lambda: len([item for item in notices if item['type'] == 'extension_dialog']) == 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None and not broker._pending and not broker.custom.views
        assert runtime.snapshot()['extensions'][0]['state'] == 'failed'
    finally:
        broker.close()
        await runtime.aclose()
