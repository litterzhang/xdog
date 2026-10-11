"""Actual original RPC client menus in noncommand callback scopes, without editor grants."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent.extensions.command_catalog import page
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.agent.tests.test_extension_menu_scope import invoke
from packages.coding.tests.test_rpc_extensions import close, response, session


async def wait(predicate):
    async with asyncio.timeout(8):
        while not predicate():
            await asyncio.sleep(.003)


@pytest.mark.parametrize('scope', ['tool', 'event', 'pre', 'post', 'input', 'context'])
async def test_actual_rpc_callback_reads_only_client_menu_under_independent_grant(tmp_path, scope):
    owner, calls = session(tmp_path, extra=('menureader',))
    owner.extensions.settings['menureader'] = {'scope': scope}
    events, menu = [], {f'/client-only-{i}': 'Not registered on server' for i in range(40)}
    rpc = RpcExtensions(owner, events.append)
    rpc.negotiate({'extension_command_catalog': 1})
    task = None
    try:
        async with rpc.bind('original-turn'):
            task = asyncio.create_task(invoke(owner.extensions, scope))
            answered = set()
            async with asyncio.timeout(10):
                while not task.done():
                    for event in list(events):
                        if event['type'] != 'extension_dialog' or event['dialog']['id'] in answered:
                            continue
                        dialog = event['dialog']
                        assert dialog['operation'] == 'commands' and dialog['command_menu']['child_id'] == ''
                        value = page(menu, dialog, session_id=owner.session_id, owner='actual-client-menu')
                        with pytest.raises(ValueError, match='another session'):
                            rpc.answer(response(event, {**value, 'session_id': 'foreign'}))
                        rpc.answer(response(event, value))
                        answered.add(dialog['id'])
                    await asyncio.sleep(.003)
            await task
            rows = owner.extensions.entries.for_extension('menureader')
            assert rows[-1]['data']['available'] and set(rows[-1]['data']['names']) == set(menu)
        assert not rpc.editor_enabled and not rpc.navigation_enabled and not rpc.dialogs_enabled
        assert not owner.messages and not calls and owner.permissions.mode == 'deny'
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await rpc.aclose()
        await close(owner)


@pytest.mark.parametrize('enabled', [False, True])
async def test_actual_rpc_menu_accepted_reply_cannot_survive_withdraw_reenable(tmp_path, enabled):
    owner, calls = session(tmp_path, extra=('menureader',))
    events, entered, release = [], threading.Event(), threading.Event()
    rpc = RpcExtensions(owner, events.append)
    rpc.negotiate({'extension_command_catalog': int(enabled), 'extension_editor': 1})
    original = owner.extension_dialogs.ask
    async def held(extension, dialog):
        result = await original(extension, dialog)
        entered.set()
        async with asyncio.timeout(5):
            while not release.is_set():
                await asyncio.sleep(.003)
        return result
    owner.extension_dialogs.ask = held
    task = None
    try:
        async with rpc.bind('original-turn'):
            task = asyncio.create_task(invoke(owner.extensions, 'tool'))
            if enabled:
                await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
                event = next(event for event in events if event['type'] == 'extension_dialog')
                rpc.answer(response(event, page({'/client': 'Original'}, event['dialog'],
                    session_id=owner.session_id, owner='client')))
                await wait(entered.is_set)
                rpc.negotiate({'extension_command_catalog': 0})
                rpc.negotiate({'extension_command_catalog': 1})
            release.set()
            await task
        assert not owner.extensions.entries.for_extension('menureader')[-1]['data']['available']
        assert not calls and not owner.messages
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await rpc.aclose()
        await close(owner)


def test_actual_cli_model_tool_reads_client_only_menu_without_command_invocation(tmp_path):
    import os
    import queue
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root, home = tmp_path / 'extensions', tmp_path / 'home'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/menureader', root / 'menureader')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['menureader'], 'extension_dir': str(root)}))
    process = subprocess.Popen([sys.executable, str(Path(__file__).parent / 'fixtures/rpc_menu_client.py')],
        cwd=tmp_path, text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'CODING_DIR': str(home), 'MENU_CALLS': str(tmp_path / 'calls')})
    incoming, events = queue.Queue(), []
    def collect():
        for line in process.stdout:
            incoming.put(json.loads(line))
    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()
    def read():
        event = incoming.get(timeout=8)
        events.append(event)
        return event
    try:
        ready = read()
        assert ready['type'] == 'ready'
        send({'type': 'rpc_hello', 'extension_command_catalog': 1})
        while read()['type'] != 'rpc_capabilities':
            pass
        send({'type': 'prompt', 'message': 'Read metadata from your owned tool', 'request_id': 'turn',
              'session_id': ready['session_id']})
        dialogs = 0
        while True:
            event = read()
            if event['type'] == 'extension_dialog':
                dialogs += 1
                assert event['dialog']['command_menu']['method'] == 'tools/call'
                value = page({'/actual-client-only': 'Client owned metadata'}, event['dialog'],
                             session_id=ready['session_id'], owner='original-cli-client')
                send(response(event, value))
            assert event['type'] != 'error', event
            if event['type'] == 'message_end':
                break
        assert dialogs == 2 and (tmp_path / 'calls').read_text().count('owned-model-step') == 2
        assert not any(event['type'] == 'extension_result' for event in events)
        send({'type': 'quit'})
        assert process.wait(timeout=5) == 0, process.stderr.read()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
        reader.join(3)
        assert not reader.is_alive()


async def test_real_rpc_custom_input_reads_owned_menu_while_render_remains_unavailable(tmp_path):
    from packages.coding.tests.test_rpc_custom_views import frame_input
    from packages.coding.tests.test_rpc_extensions import packet
    owner, calls = session(tmp_path, extra=('menureader',))
    events = []
    rpc = RpcExtensions(owner, events.append)
    rpc.negotiate({'extension_custom_views': 1, 'extension_command_catalog': 1})
    try:
        rpc.start(packet(owner, 'view', 'ext-menureader-custom'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        custom = next(event for event in events if event['type'] == 'extension_dialog')
        rpc.custom_input(frame_input(custom, custom['custom_generation'], 1))
        await wait(lambda: any(event['type'] == 'extension_custom_frame' for event in events))
        frame = next(event for event in events if event['type'] == 'extension_custom_frame')
        assert not any(event.get('dialog', {}).get('operation') == 'commands' for event in events)
        rpc.custom_input(frame_input(custom, custom['custom_generation'], 2, kind='key', painted=frame['revision']))
        answered = set()
        reconfirmed = False
        async with asyncio.timeout(10):
            while not rpc.task.done():
                for event in list(events):
                    if (event['type'] != 'extension_dialog' or event['dialog'].get('operation') != 'commands'
                            or event['dialog']['id'] in answered):
                        continue
                    assert event['dialog']['command_menu']['method'] == 'xdog/custom_frame'
                    assert event['dialog']['parent_id'] == custom['dialog']['id']
                    rpc.answer(response(event, page({'/custom-client': 'Actual original client menu'},
                        event['dialog'], session_id=owner.session_id, owner='client')))
                    answered.add(event['dialog']['id'])
                refreshed = rpc.custom_frames.get(custom['dialog']['id'])
                if (not reconfirmed and len(answered) == 2 and refreshed is not None
                        and refreshed['revision'] > frame['revision']):
                    # Nested metadata RPC retires the old frame receipt. A fresh
                    # original input confirms completion; do not weaken that fence.
                    rpc.custom_input(frame_input(custom, custom['custom_generation'], 3,
                        kind='key', painted=refreshed['revision']))
                    reconfirmed = True
                await asyncio.sleep(.003)
        await rpc.task
        row = owner.extensions.entries.for_extension('menureader')[-1]['data']
        assert row['scope'] == 'custom-input' and row['names'] == ['/custom-client'] and row['available']
        assert len(answered) == 2 and not calls and not owner.messages
    finally:
        await rpc.aclose()
        await close(owner)


async def test_real_child_runtime_does_not_inherit_parent_rpc_menu_binding(tmp_path):
    from packages.coding.tests.test_rpc_children import close as close_children
    from packages.coding.tests.test_rpc_children import control, setup
    owner, adapter, nav, sources, children, events = setup(tmp_path, extension='menureader')
    rpc = RpcExtensions(owner, lambda event: None)
    rpc.negotiate({'extension_command_catalog': 1})
    try:
        async with rpc.bind('parent-original'):
            assert owner.extensions.command_menu_handler is not None
            await control(children, 'start', task='hold')
            child = next(event['child_id'] for event in events if event['type'] == 'child_result')
            await wait(adapter.entered.is_set)
            runtime = owner.children.extensions.owners[child]
            assert runtime.command_menu_handler is None
            await invoke(runtime, 'tool')
            row = runtime.entries.for_extension('menureader')[-1]['data']
            assert not row['available'] and not row['names'] and row['child_id'] == child
            assert len(adapter.contexts) == 1 and not owner.messages
    finally:
        await rpc.aclose()
        await close_children(owner, nav, sources, children)
