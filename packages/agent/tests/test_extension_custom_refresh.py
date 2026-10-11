from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from xdog.agent.extensions.api import ExtensionAPI
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.runtime import ExtensionRuntime
from xdog.agent.mcp.config import McpServerConfig
from xdog.agent.mcp.stdio import StdioClient


def request(kind='resize', *, width=40, height=5, painted=-1):
    result = {'kind': kind, 'width': width, 'height': height, 'painted': painted}
    if kind == 'key':
        result.update(key='enter', ctrl=False, alt=False, shift=False)
    return result


def dialog():
    return {'id': 'worker', 'view_id': 'view-1', 'kind': 'custom', 'title': 'Custom view'}


async def until(predicate):
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_api_repaint_is_invocation_owned_coalesced_and_closed_fenced():
    api, notices = ExtensionAPI({}), []
    api._render_notify = notices.append
    assert not api.request_render()
    api._call_id.set('call')
    api._dialog_events['call'] = asyncio.Event()
    api._has_ui.set(True)
    api._has_custom.set(True)
    task = asyncio.create_task(api.custom('view', lambda w, h: ['data'], lambda e: None))
    await until(lambda: 'call' in api._dialogs)
    assert not api.request_render()  # Custom UI alone does not promise background repaint support.
    api._has_refresh.set(True)
    for _ in range(100):
        assert api.request_render()
    await asyncio.sleep(0)
    assert len(notices) == 1
    assert notices[0]['call_id'] == 'call'
    api._call_id.set('foreign')
    assert not api.request_render()
    api._call_id.set('frame')
    api._frame_owners['frame'] = 'call', api._dialogs['call'][0]['id']
    assert api.request_render()
    api._custom_phase.set('render')
    with pytest.raises(ValueError, match='cannot request'):
        api.request_render()
    api._custom_phase.set('')
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert not api.request_render()
    assert not api._render_scheduled


async def test_broker_repaint_bursts_defer_while_hidden_and_never_complete():
    broker, notices, renders = DialogBroker(), [], []
    broker.publish = notices.append
    gate, started = asyncio.Event(), asyncio.Event()

    async def render(value):
        renders.append(value)
        if len(renders) == 2:
            started.set()
            await gate.wait()
        return {'lines': ['live']}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert not broker.refresh_custom('foreign', 'view-1')
    for _ in range(100):
        assert broker.refresh_custom('fixture', 'view-1')
    assert not renders  # No fabricated viewport before the actual viewer's resize.
    assert broker.request_custom(identifier, request())
    await started.wait()
    for _ in range(100):
        assert broker.refresh_custom('fixture', 'view-1')
    view = broker.custom.views[identifier]
    assert view.count == 1 and view.refresh_pending
    gate.set()
    await until(lambda: len(renders) == 3 and view.count == 0)
    assert all(value['kind'] == 'resize' for value in renders)
    assert not task.done()
    broker.custom.suspend(identifier, True)
    count = len(renders)
    for _ in range(100):
        assert broker.refresh_custom('fixture', 'view-1')
    await asyncio.sleep(0.06)
    assert len(renders) == count
    broker.custom.suspend(identifier, False)
    await until(lambda: len(renders) == count + 1)
    assert broker.request_custom(identifier, request(height=0))
    await until(lambda: view.geometry[1] == 0)
    count = len(renders)
    assert broker.refresh_custom('fixture', 'view-1')
    await asyncio.sleep(0.06)
    assert len(renders) == count
    assert broker.request_custom(identifier, request(height=4))
    await until(lambda: len(renders) == count + 2)
    broker.resolve(identifier, None)
    assert await task is None
    assert not broker.refresh_custom('fixture', 'view-1') and not broker.custom.views


async def test_background_state_change_revokes_inflight_completion_then_requires_fresh_frame():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    gate, started = asyncio.Event(), asyncio.Event()

    async def render(value):
        if value['kind'] == 'key':
            started.set()
            await gate.wait()
            return {'lines': ['new data'], 'done': True, 'value': 'chosen'}
        return {'lines': ['new data']}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert broker.request_custom(identifier, request())
    await until(lambda: len(notices) == 2)
    assert broker.request_custom(identifier, request('key', painted=1))
    await started.wait()
    assert broker.refresh_custom('fixture', 'view-1')
    gate.set()
    await until(lambda: len(notices) == 4)
    assert not task.done() and notices[2]['review_required']
    assert broker.request_custom(identifier, request('key', painted=1))
    await until(lambda: len(notices) == 5)
    assert not task.done()
    assert broker.request_custom(identifier, request('key', painted=4))
    assert await task == 'chosen'


async def test_actual_worker_background_repaint_old_client_and_retired_view_are_fenced(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/liveview', root / 'liveview')
    runtime = ExtensionRuntime(['liveview'], root, tmp_path)
    broker, notices = DialogBroker(), []
    runtime.custom_handler, runtime.custom_refresh_handler = broker.ask_custom, broker.refresh_custom
    broker.publish = notices.append
    try:
        await runtime.initialize()
        client = runtime._manager._clients['liveview']
        command = asyncio.create_task(runtime.command('ext-liveview-view', ''))
        await until(lambda: notices)
        identifier = notices[0]['dialog']['id']
        assert broker.request_custom(identifier, request())
        await until(lambda: any('LIVE-STATE=2;INPUTS=0' in str(item) for item in notices))
        assert len([item for item in notices if item['type'] == 'extension_custom_frame']) == 3
        call_id, dialog_id = next(iter(client._custom_views))
        notice = {'jsonrpc': '2.0', 'method': 'xdog/custom_refresh', 'params': {
            'call_id': call_id, 'dialog_id': dialog_id, 'view_id': client._custom_views[call_id, dialog_id]}}
        assert broker.request_custom(identifier, request('key', painted=3))
        assert '"inputs": 1' in await command
        count = len(notices)
        assert client._internal_notification(notice)
        await asyncio.sleep(0.05)
        assert len(notices) == count and not client._custom_views
        await runtime.manage('reload', 'liveview')
        assert runtime._manager._clients['liveview'] is not client
        called = []
        runtime.custom_refresh_handler = lambda *args: called.append(args) or True
        client._custom_views[call_id, dialog_id] = notice['params']['view_id']
        assert client._internal_notification(notice) and not called
    finally:
        broker.close()
        await runtime.aclose()


async def test_repaint_revokes_queued_completion_before_its_callback_starts():
    broker, notices = DialogBroker(), []
    broker.publish = notices.append

    async def render(value):
        return {'lines': ['changed'], 'done': value['kind'] == 'key', 'value': 'selected'}

    task = asyncio.create_task(broker.ask_custom('fixture', dialog(), render))
    await until(lambda: notices)
    identifier = notices[0]['dialog']['id']
    assert broker.request_custom(identifier, request())
    await until(lambda: len(notices) == 2)
    # Both are admitted before yielding. The queued key must not authorize new
    # unseen state even though it captures the newly incremented proof at startup.
    assert broker.request_custom(identifier, request('key', painted=1))
    assert broker.refresh_custom('fixture', 'view-1')
    await until(lambda: len(notices) == 4)
    assert not task.done() and notices[2]['review_required']
    assert broker.request_custom(identifier, request('key', painted=3))
    assert await task == 'selected'


async def test_ordinary_current_mcp_rejects_private_extension_repaint_notification(tmp_path):
    client = StdioClient(McpServerConfig('peer', ('unused',)), tmp_path)
    client.modern = True
    client.state = 'connected'
    stdout = asyncio.StreamReader()
    client.process = SimpleNamespace(stdout=stdout)
    stdout.feed_data((json.dumps({'jsonrpc': '2.0', 'method': 'xdog/custom_refresh', 'params': {
        'call_id': 'one', 'dialog_id': 'two', 'view_id': 'three'}}) + '\n').encode())
    await client._read()
    assert client.state == 'disconnected' and client.error == 'McpError'
