"""Original-invocation read-only menu handler, independent from editor/session grants."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path

import pytest
from xdog.agent.extensions.command_catalog import page
from xdog.agent.extensions.runtime import ExtensionRuntime, silent_extension_ui
from xdog.ai.types import UserMessage


def setup(tmp_path, scope='tool', child=''):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parent / 'fixtures/extensions/menureader', root / 'menureader')
    return ExtensionRuntime(['menureader'], root, tmp_path, settings={'menureader': {'scope': scope}},
        context=lambda: {'session_id': 'owned-session', 'child_id': child})


async def invoke(runtime, scope):
    await runtime.initialize()
    if scope == 'command':
        await runtime.command('ext-menureader-inspect', '')
    elif scope == 'tool':
        await next(tool for tool in runtime.tools() if 'menureader' in tool.name).execute('owned-tool', {})
    elif scope == 'event':
        await runtime.event('turn_start', {'host_command_menu': False, 'session_id': 'forged'})
    elif scope == 'pre':
        assert await runtime.pre('safe', {}) == {}
    elif scope == 'post':
        assert await runtime.post('safe', {}, 'original') == 'original'
    elif scope == 'input':
        value = UserMessage(content='original')
        assert await runtime.input(value) == value
    else:
        assert await runtime.transform([]) == []


@pytest.mark.parametrize('scope', ['command', 'tool', 'event', 'pre', 'post', 'input', 'context'])
async def test_real_worker_menu_pages_original_scope_no_editor_or_command_execution(tmp_path, scope):
    runtime, requests = setup(tmp_path, scope), []
    menu = {f'/actual-host-{i}': 'HOST metadata' for i in range(40)}
    async def handler(extension, dialog):
        requests.append(dialog)
        assert dialog['command_menu']['session_id'] == 'owned-session'
        assert dialog['command_menu']['child_id'] == ''
        return page(menu, dialog, session_id='owned-session', owner=dialog['command_menu']['invocation'])
    runtime.command_menu_handler = handler
    try:
        await invoke(runtime, scope)
        result = runtime.entries.for_extension('menureader')[-1]['data']
        assert result['scope'] == scope and result['available'] and set(result['names']) == set(menu)
        assert result['executed'] == 0 and len(requests) == 3
        assert len({row['command_menu']['invocation'] for row in requests}) == 1
        assert not runtime.dialog_handler and not runtime.catalog_handler
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('change', ['handler', 'session', 'foreign-page'])
async def test_actual_waited_read_cannot_publish_old_handler_owner_or_foreign_metadata(tmp_path, change):
    runtime, entered, release = setup(tmp_path), threading.Event(), threading.Event()
    async def handler(extension, dialog):
        entered.set()
        async with asyncio.timeout(5):
            while not release.is_set():
                await asyncio.sleep(.003)
        return page({'/original': 'metadata'}, dialog,
                    session_id='foreign' if change == 'foreign-page' else 'owned-session', owner='original')
    runtime.command_menu_handler = handler
    task = asyncio.create_task(invoke(runtime, 'tool'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.003)
        if change == 'handler':
            async def replacement(*args):
                raise AssertionError('No automatic read replay')
            runtime.command_menu_handler = replacement
        elif change == 'session':
            runtime.context = lambda: {'session_id': 'replacement'}
        release.set()
        await task
        result = runtime.entries.for_extension('menureader')[-1]['data']
        assert not result['available'] and not result['names']
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()


async def test_headless_and_provider_terminal_cannot_borrow_installed_menu_handler(tmp_path):
    from xdog.agent.extensions.providers import ExtensionProviderStream
    from xdog.ai.types import Context, StreamOptions
    runtime, calls = setup(tmp_path), []
    async def handler(*args):
        calls.append(args)
        raise AssertionError('No UI authority')
    runtime.command_menu_handler = handler
    try:
        with silent_extension_ui():
            await invoke(runtime, 'tool')
            value = json.loads(await runtime.command('ext-menureader-inspect', ''))
            assert not value['available']
        assert await runtime.terminal_input('x', kind='key', surface='editor') == {'consume': False, 'data': 'x'}
        def base(*args):
            raise AssertionError('No provider fallback')
        stream = ExtensionProviderStream(runtime, base)('ext-menureader-offline/scope',
            Context(messages=(UserMessage(content='provider'),)), StreamOptions())
        assert [event async for event in stream][-1].type == 'done'
        assert not calls
    finally:
        await runtime.aclose()


async def test_child_requires_explicit_own_menu_binding_not_parent_or_registered_commands(tmp_path):
    runtime = setup(tmp_path, child='original-child')
    try:
        await invoke(runtime, 'tool')
        assert not runtime.entries.for_extension('menureader')[-1]['data']['available']
        async def own_menu(extension, dialog):
            assert dialog['command_menu']['child_id'] == 'original-child'
            return page({'/child-only': 'Explicit child host menu'}, dialog,
                        session_id='owned-session', owner='child-owned-ui')
        runtime.command_menu_handler = own_menu
        await invoke(runtime, 'tool')
        result = runtime.entries.for_extension('menureader')[-1]['data']
        assert result['available'] and result['names'] == ['/child-only'] and result['child_id'] == 'original-child'
    finally:
        await runtime.aclose()


async def test_cancelled_menu_read_settles_owned_handler_without_replay_or_success_page(tmp_path):
    runtime, entered, settled = setup(tmp_path), threading.Event(), threading.Event()
    calls = []
    async def handler(extension, dialog):
        calls.append(dialog)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            settled.set()
    runtime.command_menu_handler = handler
    task = asyncio.create_task(invoke(runtime, 'tool'))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(.003)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert settled.is_set() and len(calls) == 1 and not runtime.entries.for_extension('menureader')
    finally:
        await runtime.aclose()
