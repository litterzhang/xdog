"""UI-041 scope audit: unavailable command grants are not borrowed by other callbacks.

This is negative authority evidence, not proof that broader reviewed proposals are
implemented. The historical comparison and positive gaps live in the sidecar audit.
The private fixture deliberately gives the runtime live dialog/catalog handlers:
absence of a frontend must not be the reason a non-command callback is refused.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
from textwrap import dedent

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.extensions.dialogs import DialogBroker
from xdog.agent.extensions.inspection import capture
from xdog.agent.extensions.runtime import ExtensionRuntime, silent_extension_ui
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream

SOURCE = '''
def setup(api):
    async def probe(scope, context):
        denied = {
            'editor': await api.get_editor(),
            'replace': await api.set_editor('DO-NOT-ADOPT', '0' * 64),
            'session': await api.get_session(),
            'rename': await api.set_session_name('DO-NOT-RENAME'),
            'menu': await api.get_command_catalog(),
            'sessions': await api.list_sessions(),
            'tree': await api.get_session_tree(),
            'browse': await api.open_sessions(),
            'navigate': await api.navigate_session('new'),
        }
        api.append_entry('scope_probe', {
            'scope': scope, 'denied': denied, 'model': await api.get_model(),
            'name': await api.get_session_name(), 'child_id': context.get('child_id', ''),
        })
        return 'PROBED'

    async def command(args, context):
        return await probe('headless-command', context)

    async def tool(args, context):
        return await probe('tool', context)

    async def event(context):
        await probe('event', context)

    async def pre(name, arguments, context):
        await probe('pre', context)
        return arguments

    async def post(name, arguments, result, context):
        await probe('post', context)
        return result

    async def transform(messages, context):
        await probe('context', context)
        return messages

    async def input_hook(text, metadata, context):
        await probe('input', context)
        return {'action': 'continue'}

    async def terminal(data, metadata, context):
        await probe('terminal', context)
        return {'consume': False}

    async def custom(args, context):
        async def render(width, height):
            await probe('custom-render', context)
            return {'lines': ['scope probe']}
        async def on_input(event):
            await probe('custom-input', context)
            return {'done': True, 'value': 'DONE'}
        return str(await api.custom('Scope verification', render, on_input))

    api.command('probe', 'Probe headless authority', command)
    api.command('custom', 'Probe nested frame authority', custom)
    api.tool('probe', 'Probe non-command authority', {'type': 'object'}, tool)
    api.on('turn_start', event)
    api.before_tool(pre)
    api.after_tool(post)
    api.context(transform)
    api.on_input(input_hook)
    api.on_terminal_input(terminal)
'''

DENIED = {'editor': None, 'replace': False, 'session': None, 'rename': False,
          'menu': None, 'sessions': None, 'tree': None, 'browse': False, 'navigate': False}


def setup(tmp_path, child):
    root = tmp_path / 'extensions'
    fixture = root / 'scopeprobe'
    fixture.mkdir(parents=True)
    (fixture / 'extension.yaml').write_text('name: scopeprobe\nentry_point: main\n')
    (fixture / 'main.py').write_text(dedent(SOURCE))
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    agent = Agent(stream, config=AgentConfig(model='offline/owned'))
    child_id = 'child-owned' if child else ''
    owner_id = child_id or 'session-owned'
    runtime = ExtensionRuntime(['scopeprobe'], root, tmp_path,
        context=lambda: {'session_id': 'session-owned', 'child_id': child_id},
        inspection=lambda: capture(agent, 'session-owned', owner_id))
    runtime.session_name = lambda: {'session_id': 'session-owned', 'owner_id': owner_id, 'name': 'Owned name'}
    requests = []

    async def reject(extension, dialog):
        requests.append(dialog)
        raise AssertionError('Non-command scope reached a command-only host handler')

    runtime.dialog_handler = reject
    runtime.catalog_handler = reject
    return runtime, agent, calls, requests


def verify(runtime, scopes, child):
    rows = [row['data'] for row in runtime.entries.for_extension('scopeprobe')]
    assert {row['scope'] for row in rows} == scopes
    assert all(row['denied'] == DENIED for row in rows)
    assert all(row['name'] == 'Owned name' for row in rows)
    assert all(row['model']['session_id'] == 'session-owned' for row in rows)
    assert all(row['model']['id'] == 'offline/owned' for row in rows)
    assert all(row['child_id'] == ('child-owned' if child else '') for row in rows)
    assert runtime.snapshot()['extensions'][0]['state'] == 'active'


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child-envelope'])
@pytest.mark.parametrize('silent', [False, True], ids=['ui-handlers', 'headless-with-handlers'])
async def test_real_worker_noncommand_editor_host_grants_cannot_be_forged_or_borrowed(tmp_path, child, silent):
    runtime, agent, calls, requests = setup(tmp_path, child)
    scopes = {'event', 'tool', 'pre', 'post', 'context', 'input', 'terminal'}
    try:
        with silent_extension_ui() if silent else nullcontext():
            await runtime.initialize()
            await runtime.event('turn_start', {'host_editor': True, 'host_session': True,
                'host_catalog': True, 'host_navigation': True, 'session_id': 'foreign', 'child_id': 'foreign'})
            result = await runtime.tools()[0].execute('probe', {})
            assert result.content[0].text == 'PROBED'
            assert await runtime.pre('safe', {}) == {}
            assert await runtime.post('safe', {}, 'original') == 'original'
            message = UserMessage(content='original')
            assert await runtime.transform([message]) == [message]
            assert await runtime.input(message) == message
            assert await runtime.terminal_input('x', kind='key', surface='editor') == {'consume': False, 'data': 'x'}
            if silent:
                assert await runtime.command('ext-scopeprobe-probe', '') == 'PROBED'
                scopes.add('headless-command')
        verify(runtime, scopes, child)
        assert not requests and not calls and not agent.state.messages
    finally:
        await runtime.aclose()


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child-envelope'])
async def test_real_worker_custom_render_and_input_do_not_inherit_suspended_command_host_grants(tmp_path, child):
    runtime, agent, calls, requests = setup(tmp_path, child)
    broker, notices = DialogBroker(), []
    broker.publish = notices.append
    runtime.custom_handler = broker.ask_custom
    task = asyncio.create_task(runtime.command('ext-scopeprobe-custom', ''))

    async def until(predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.003)

    try:
        await until(lambda: notices)
        identifier = notices[0]['dialog']['id']
        assert broker.request_custom(identifier, {'kind': 'resize', 'width': 40, 'height': 5, 'painted': -1})
        await until(lambda: any(row['type'] == 'extension_custom_frame' for row in notices))
        frame = next(row for row in notices if row['type'] == 'extension_custom_frame')
        assert broker.request_custom(identifier, {'kind': 'key', 'key': 'enter', 'ctrl': False,
            'alt': False, 'shift': False, 'width': 40, 'height': 5, 'painted': frame['revision']})
        assert await asyncio.wait_for(task, 5) == 'DONE'
        verify(runtime, {'custom-render', 'custom-input'}, child)
        assert not requests and not calls and not agent.state.messages and not broker.custom.views
    finally:
        broker.close()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await runtime.aclose()
