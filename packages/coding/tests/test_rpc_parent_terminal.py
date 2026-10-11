"""Parent hook RPC is opt-in editing data, not prompt execution authority."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.terminal import RpcTerminal

from packages.coding.tests.test_rpc_extensions import close, session


def request(controller, key='input', data='ab', kind='key'):
    return {'request_id': key, 'session_id': controller.session.session_id,
            'session_revision': controller.navigation.revision, 'terminal_owner': controller.owner,
            'worker_generation': controller.session.extensions.generation, 'kind': kind, 'data': data}


@pytest.mark.parametrize('kind,data,expected', [('key', 'ab', '[a][b]'),
                                              ('paste', '开发🙂\nline', '[开发🙂\nline]'),
                                              ('paste', 'drop', None)])
async def test_actual_parent_worker_transform_never_submits_prompt(tmp_path, kind, data, expected):
    owner, calls = session(tmp_path, extra=('terminal',))
    events = []
    controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    try:
        await owner.initialize_extensions()
        with pytest.raises(ValueError):
            controller.start(request(controller))
        controller.negotiate({'terminal_input': 1})
        controller.start(request(controller, kind=kind, data=data))
        await controller.task
        result = next(event for event in events if event['type'] == 'terminal_result')
        assert result['success'] and result['consume'] is (expected is None)
        assert result.get('data') == expected
        assert not owner.messages and not calls and owner.permissions.mode == 'deny'
        assert await owner.extensions.command('ext-terminal-count', '') == '1'
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('change', ['early-cancel', 'withdraw', 'foreign'])
async def test_parent_terminal_revocation_receipts_and_foreign_owner(tmp_path, change):
    owner, calls = session(tmp_path, extra=('terminal',))
    events = []
    controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    try:
        await owner.initialize_extensions()
        controller.negotiate({'terminal_input': 1})
        packet = request(controller)
        if change == 'foreign':
            for field in ('session_id', 'session_revision', 'terminal_owner', 'worker_generation'):
                with pytest.raises(ValueError):
                    controller.start({**packet, field: 'foreign'})
            return
        controller.start(packet)
        if change == 'early-cancel':
            controller.cancel(packet)
        else:
            controller.negotiate({'terminal_input': 0})
        await asyncio.gather(controller.task, return_exceptions=True)
        await asyncio.sleep(0)
        receipts = [event for event in events if event['type'] == 'terminal_result']
        assert len(receipts) == 1 and receipts[0]['canceled'] and 'data' not in receipts[0]
        assert await owner.extensions.command('ext-terminal-count', '') == '0'
        assert not calls and not owner.messages
    finally:
        await controller.aclose()
        await close(owner)


def test_actual_cli_parent_terminal_negotiation_transform_and_no_model(tmp_path):
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
        peer.send({'type': 'extension_control', 'request_id': 'load', 'session_id': peer.ready['session_id'],
                   'action': 'list', 'name': '', 'args': ''})
        assert peer.read('extension_result')['success']
        peer.send({'type': 'rpc_hello', 'terminal_input': 1})
        caps = peer.read('terminal_capabilities')
        assert caps['active'] and caps['terminal_input'] == 1
        packet = {key: caps[key] for key in ('session_id', 'session_revision', 'terminal_owner', 'worker_generation')}
        peer.send({'type': 'terminal_input', **packet, 'request_id': 'input', 'kind': 'key', 'data': 'ab'})
        result = peer.read('terminal_result')
        assert result['success'] and result['data'] == '[a][b]' and not result['consume']
        peer.send({'type': 'status'})
        assert peer.read('status')['messages'] == 0 and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()


@pytest.mark.parametrize('outcome', ['cancel', 'timeout', 'unsafe', 'approval'])
async def test_actual_parent_hook_failure_and_approval_do_not_publish_edit_or_replay(tmp_path, outcome):
    from xdog.agent import AgentContext, BeforeToolCallContext
    from xdog.ai.types import AssistantMessage, ToolCall

    owner, calls = session(tmp_path, extra=('terminal',))
    events = []
    controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    entered, release = asyncio.Event(), asyncio.Event()
    approval = None
    try:
        await owner.initialize_extensions()
        process = owner.extensions._manager._clients['terminal'].process
        controller.negotiate({'terminal_input': 1})
        original = owner.extensions.terminal_input
        async def held(*args, **kwargs):
            if outcome == 'unsafe':
                return {'consume': False, 'data': '\r'}
            result = await original(*args, **kwargs)
            if outcome == 'approval':
                entered.set()
                await release.wait()
            return result
        owner.extensions.terminal_input = held
        packet = request(controller, data='hang' if outcome in {'cancel', 'timeout'} else 'ab')
        controller.start(packet)
        if outcome == 'cancel':
            await asyncio.sleep(0.1)
            controller.cancel(packet)
        elif outcome == 'approval':
            async with asyncio.timeout(5):
                await entered.wait()
            owner.permissions.set_mode('ask')
            owner.permissions.set_request_handler(lambda request: None)
            call = ToolCall(id='approval', name='filesystem', arguments={'action': 'write', 'path': 'target'})
            approval = asyncio.create_task(owner.permissions.before_tool_call(BeforeToolCallContext(
                assistant_message=AssistantMessage(), tool_call=call, args=call.arguments, context=AgentContext())))
            async with asyncio.timeout(3):
                while not owner.permissions.has_pending_requests:
                    await asyncio.sleep(0.003)
            release.set()
        async with asyncio.timeout(7):
            await controller.task
        receipt = next(event for event in events if event['type'] == 'terminal_result')
        assert not receipt['success'] and 'data' not in receipt and 'consume' not in receipt
        if outcome in {'cancel', 'timeout'}:
            assert process.returncode is not None
        assert not calls and not owner.messages and not (tmp_path / 'target').exists()
        with pytest.raises(ValueError):
            controller.start(packet)  # Used identity never becomes an implicit retry.
    finally:
        release.set()
        owner.permissions.deny_all()
        if approval is not None:
            await approval
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('timing', ['before', 'after-hook'])
async def test_parent_terminal_cannot_borrow_unpublished_child_dialog_input(tmp_path, timing):
    from xdog.agent.extensions.dialogs import DialogBroker

    owner, calls = session(tmp_path, extra=('terminal',))
    events = []
    controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    broker = DialogBroker()
    owner.children.extensions._dialogs['owned-child'] = broker
    entered, release = asyncio.Event(), asyncio.Event()
    waiting = None
    try:
        await owner.initialize_extensions()
        controller.negotiate({'terminal_input': 1})
        if timing == 'after-hook':
            original = owner.extensions.terminal_input
            async def held(*args, **kwargs):
                result = await original(*args, **kwargs)
                entered.set()
                await release.wait()
                return result
            owner.extensions.terminal_input = held
            controller.start(request(controller))
            async with asyncio.timeout(5):
                await entered.wait()
        # Actual broker admission, deliberately no frontend publication.
        waiting = asyncio.create_task(broker.ask('fixture', {
            'id': 'worker', 'kind': 'input', 'title': 'Owned child question'}))
        async with asyncio.timeout(3):
            while not broker.has_pending:
                await asyncio.sleep(0.003)
        assert owner.children.extensions.has_pending_dialogs
        if timing == 'before':
            with pytest.raises(ValueError, match='dialog'):
                controller.start(request(controller))
            assert await owner.extensions.command('ext-terminal-count', '') == '0'
        else:
            release.set()
            await controller.task
            result = next(event for event in events if event['type'] == 'terminal_result')
            assert not result['success'] and 'data' not in result and 'consume' not in result
            assert await owner.extensions.command('ext-terminal-count', '') == '1'
        assert not calls and not owner.messages and owner.permissions.mode == 'deny'
    finally:
        release.set()
        broker.close()
        if waiting is not None:
            await waiting
        owner.children.extensions._dialogs.pop('owned-child', None)
        await controller.aclose()
        await close(owner)


async def test_parent_release_requires_both_client_and_worker_opt_in_and_never_returns_edit_data(tmp_path):
    from packages.agent.tests.test_extension_terminal_input import add_release_observer
    owner, calls = session(tmp_path, extra=('terminal',))
    add_release_observer(tmp_path / 'extensions/terminal/main.py', tmp_path / 'release.jsonl')
    events = []
    controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    try:
        await owner.initialize_extensions()
        controller.negotiate({'terminal_input': 1})
        with pytest.raises(ValueError):
            controller.start(request(controller, data='\x1b[97;1:3u', kind='release'))
        controller.negotiate({'terminal_release_input': 1})
        assert events[-1]['terminal_events'] == ['key', 'paste', 'release']
        controller.start(request(controller, data='\x1b[97;1:3u', kind='release'))
        await controller.task
        result = events[-1]
        assert result['type'] == 'terminal_result' and result['success'] and result['consume']
        assert 'data' not in result and not calls and not owner.messages
        assert await owner.extensions.command('ext-terminal-count', '') == '0'
        assert json.loads((tmp_path / 'release.jsonl').read_text())['kind'] == 'release'
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('change', ['withdraw', 'cancel', 'unsafe', 'approval'])
async def test_parent_release_revocation_unsafe_transform_and_approval_never_deliver_keys(tmp_path, change):
    from packages.agent.tests.test_extension_terminal_input import add_release_observer
    owner, calls = session(tmp_path, extra=('terminal',))
    log = tmp_path / 'release.jsonl'
    add_release_observer(tmp_path / 'extensions/terminal/main.py', log,
                         behavior='transform' if change == 'unsafe' else 'hang')
    events = []
    controller = RpcTerminal(owner, RpcNavigation(owner, events.append), events.append)
    try:
        await owner.initialize_extensions()
        process = owner.extensions._manager._clients['terminal'].process
        controller.negotiate({'terminal_input': 1, 'terminal_release_input': 1})
        packet = request(controller, data='\x1b[97;1:3u', kind='release')
        if change == 'approval':
            from unittest.mock import PropertyMock, patch
            with patch.object(type(owner.permissions), 'has_pending_requests', new_callable=PropertyMock,
                              return_value=True), pytest.raises(ValueError):
                controller.start(packet)
            assert not log.exists()
            return
        controller.start(packet)
        async with asyncio.timeout(5):
            while not log.exists():
                await asyncio.sleep(0.005)
        if change == 'withdraw':
            controller.negotiate({'terminal_release_input': 0})
        elif change == 'cancel':
            controller.cancel(packet)
        await controller.task
        result = next(row for row in events if row['type'] == 'terminal_result')
        assert not result['success'] and 'data' not in result and not calls and not owner.messages
        assert process.returncode is not None
    finally:
        await controller.aclose()
        await close(owner)
