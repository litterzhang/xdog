"""Actual child terminal hook RPC transforms data only, not child input/tool authority."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.terminal_data import editable_data

from packages.coding.tests.test_rpc_children import close, control, packet, setup, wait


async def started_terminal(children, events):
    await control(children, 'start', task='hold')
    child = next(event['child_id'] for event in events if event['type'] == 'child_result')
    await wait(lambda: child in children.session.children.extensions.terminal_metadata(children.lease))
    await wait(lambda: children.session.children.extensions.terminal_metadata(children.lease)[child]['active'])
    await wait(lambda: bool(children.session.children.supervisor.capture_source(child).messages))
    return child


def terminal_packet(children, child, key='terminal', *, kind='key', data='ab'):
    metadata = children.session.children.extensions.terminal_metadata(children.lease)[child]
    return packet(children, key, child_id=child, kind=kind, data=data,
                  terminal_generation=children.terminal_generation, worker_generation=metadata['generation'])


async def test_actual_child_worker_hook_transform_consume_and_no_queue_submission(tmp_path):
    session, adapter, nav, sources, children, events = setup(tmp_path, extension='terminal')
    try:
        child = await started_terminal(children, events)
        await wait(adapter.entered.is_set)  # Actual first model boundary, not earlier user admission.
        with pytest.raises(ValueError):
            children.terminal_input(terminal_packet(children, child))
        children.negotiate({'child_terminal_input': 1})
        runtime = session.children.extensions.owners[child]
        for index, (kind, data, transformed) in enumerate([
            ('key', 'ab', '[a][b]'),
            ('paste', '开发🙂\nsecond line', '[开发🙂\nsecond line]'),
            ('paste', 'drop', None),
        ], 1):
            key = f'terminal-{index}'
            children.terminal_input(terminal_packet(children, child, key, kind=kind, data=data))
            await children.jobs[key][0]
            await asyncio.sleep(0)
            result = next(event for event in events
                          if event['type'] == 'child_terminal_result' and event['request_id'] == key)
            assert result['success'] and result['consume'] is (transformed is None)
            assert result.get('data') == transformed and result['child_id'] == child
            assert result['child_owner'] == children.lease
            captured = session.children.supervisor._children[child].agent
            assert captured is not None and not captured.pending_inputs()
            assert len(adapter.contexts) == 1 and not session.messages and session.permissions.mode == 'deny'
            assert await runtime.command('ext-terminal-count', '') == str(index)
    finally:
        await close(session, nav, sources, children)


@pytest.mark.parametrize('key', ['\r', '\n', '\x03', '\x04', '\x1b', '\x1bOP', '\x1bg', '\x1b[13;2u'])
def test_remote_terminal_key_contract_refuses_submit_host_controls_and_external_actions(key):
    with pytest.raises(ValueError):
        editable_data(key, 'key')
    # Same bytes are inert inside atomic paste, not reparsed as controls.
    assert editable_data(key, 'paste') == key


@pytest.mark.parametrize('change', ['withdraw', 'cancel', 'revision'])
async def test_actual_held_child_terminal_hook_settles_or_refuses_on_original_owner_change(tmp_path, change):
    session, _, nav, sources, children, events = setup(tmp_path, extension='terminal')
    try:
        child = await started_terminal(children, events)
        children.negotiate({'child_terminal_input': 1})
        runtime = session.children.extensions.owners[child]
        process = runtime._manager._clients['terminal'].process
        # Distinguish an actual in-flight hook from a canceled pre-admission coroutine.
        original = runtime.terminal_input
        entered = asyncio.Event()
        async def held(*args, **kwargs):
            entered.set()
            return await original(*args, **kwargs)
        runtime.terminal_input = held
        children.terminal_input(terminal_packet(children, child, data='hang'))
        task = children.jobs['terminal'][0]
        await entered.wait()
        await asyncio.sleep(0.05)
        if change == 'withdraw':
            children.negotiate({'child_terminal_input': 0})
        elif change == 'cancel':
            children.cancel(packet(children, 'terminal'))
        else:
            nav.refresh_revision()
            # Cancellation is still bound to captured original context, not the new revision.
            scope = children.jobs['terminal'][1]
            children.cancel(scope)
        async with asyncio.timeout(6):
            await task
        result = next(event for event in events if event['type'] == 'child_terminal_result')
        assert not result['success'] and result['canceled'] and 'data' not in result
        assert process.returncode is not None
        assert not session.messages and session.permissions.mode == 'deny'
    finally:
        await close(session, nav, sources, children)


async def test_child_terminal_rejects_foreign_or_reused_inputs_and_unsafe_hook_transform(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path, extension='terminal')
    try:
        child = await started_terminal(children, events)
        children.negotiate({'child_terminal_input': 1})
        request = terminal_packet(children, child)
        for field in ('child_id', 'child_owner', 'child_epoch', 'session_revision',
                      'terminal_generation', 'worker_generation'):
            with pytest.raises(ValueError):
                children.terminal_input({**request, field: 'foreign'})
        children.terminal_input(request)
        with pytest.raises(ValueError):
            children.terminal_input(request)
        await children.jobs['terminal'][0]
        await asyncio.sleep(0)
        with pytest.raises(ValueError):
            children.terminal_input(request)
        # Invalid callback result is an explicit failure, never raw fallback.
        children.terminal_input(terminal_packet(children, child, 'bad', data='bad'))
        await children.jobs['bad'][0]
        result = [event for event in events if event['type'] == 'child_terminal_result'][-1]
        assert not result['success'] and 'data' not in result
        assert not session.messages and session.permissions.mode == 'deny'
    finally:
        await close(session, nav, sources, children)


def test_actual_cli_child_terminal_transform_receipts_never_submit_or_authorize_input(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/terminal', root / 'terminal')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['terminal'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'child_terminal_input': 1})
        caps = peer.read('child_capabilities')
        while not caps['child_terminal_input']:
            caps = peer.read('child_capabilities')
        peer.caps = caps
        peer.send(peer.packet('start', action='start', task='hold'))
        child = peer.read('child_result')
        assert child['success']
        # Explicit current metadata read after worker admission, not guessed generations.
        import time
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            peer.send({'type': 'rpc_hello', 'child_terminal_input': 1})
            caps = peer.read('child_capabilities')
            registry = caps['child_terminal_registry'].get(child['child_id'])
            if registry and registry['active']:
                break
            time.sleep(0.01)
        else:
            raise AssertionError('Child terminal registry never became active')
        peer.caps = caps
        # Registry activity admits terminal hooks, not the model generator. Quit
        # can still cancel input/context/checkpoint work before its first step;
        # only an entered model owns the fixture's producer cleanup marker.
        deadline = time.monotonic() + 6
        while not peer.marker.exists() and time.monotonic() < deadline:
            assert peer.process.poll() is None, 'CLI exited before child model admission'
            time.sleep(0.01)
        assert peer.marker.exists(), 'Child model generator never entered'
        assert not peer.marker.with_suffix('.settled').exists()
        for key, kind, data, expected in (('keys', 'key', 'ab', '[a][b]'),
                                         ('paste', 'paste', '开发🙂\nline', '[开发🙂\nline]'),
                                         ('consume', 'paste', 'drop', None)):
            peer.send(peer.packet(key, type='child_terminal_input', child_id=child['child_id'],
                terminal_generation=caps['terminal_generation'], worker_generation=registry['generation'],
                kind=kind, data=data))
            result = peer.read('child_terminal_result')
            assert result['success'] and result.get('data') == expected
            assert result['consume'] is (expected is None) and result['child_owner'] == caps['child_owner']
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny'
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=6) == 0
        assert peer.marker.with_suffix('.settled').exists()
    finally:
        peer.close()


async def test_child_terminal_rejects_actual_unpainted_parent_approval_before_callback(tmp_path):
    from xdog.agent import AgentContext, BeforeToolCallContext
    from xdog.ai.types import AssistantMessage, ToolCall
    session, _, nav, sources, children, events = setup(tmp_path, extension='terminal')
    waiting = None
    try:
        child = await started_terminal(children, events)
        children.negotiate({'child_terminal_input': 1})
        session.permissions.set_mode('ask')
        session.permissions.set_request_handler(lambda request: None)  # Deliberately no frontend publication.
        call = ToolCall(id='parent-write', name='filesystem', arguments={'action': 'write', 'path': 'target'})
        waiting = asyncio.create_task(session.permissions.before_tool_call(BeforeToolCallContext(
            assistant_message=AssistantMessage(), tool_call=call, args=call.arguments, context=AgentContext())))
        await wait(lambda: session.permissions.has_pending_requests)
        with pytest.raises(ValueError, match='approval'):
            children.terminal_input(terminal_packet(children, child))
        assert not children.jobs
        runtime = session.children.extensions.owners[child]
        assert await runtime.command('ext-terminal-count', '') == '0'
        session.permissions.deny_all()
        assert await waiting is not None
        assert not (tmp_path / 'target').exists() and not session.messages
    finally:
        session.permissions.deny_all()
        if waiting is not None:
            await waiting
        await close(session, nav, sources, children)
