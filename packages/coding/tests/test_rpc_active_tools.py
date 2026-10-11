"""Original command tool selection review, callback/result success and host CAS."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent import AgentTool
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


async def wait(predicate):
    async with asyncio.timeout(6):
        while not predicate():
            await asyncio.sleep(.002)


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'withdraw', 'changed', 'old-client'])
async def test_worker_active_tool_selection_retains_permission_and_needs_original_review(tmp_path, ending):
    owner, calls = session(tmp_path, extra=('toolselector',))
    owner.agent.set_tools((*owner.agent.state.tools, AgentTool(name='selected', parameters={}),
                           AgentTool(name='excluded', parameters={})))
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_active_tools': int(ending != 'old-client')})
    history = owner.agent.messages
    try:
        controller.start({'request_id': 'select', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-toolselector-select', 'args': json.dumps({'names': ['selected'], 'fail': ending == 'fail'})},
                         turn_running=False)
        if ending == 'old-client':
            await controller.task
            result = next(row for row in events if row['type'] == 'extension_result')
            # Captured inventory is read-only invocation metadata, not a negotiated mutation grant.
            payload = json.loads(result['output'].removeprefix('TOOLS-QUEUED '))
            assert payload['accepted'] is False and payload['before'] == payload['after']
            assert payload['before']['session_id'] == owner.session_id
            assert payload['before']['active'] == [tool.name for tool in owner.agent.active_tools]
            assert owner.agent.active_tool_selection is None
            assert not any(row['type'] in {'extension_dialog', 'extension_active_tools_result'} for row in events)
            assert owner.permissions.mode == 'deny' and owner.agent.messages is history and not calls
            return
        await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        assert event['dialog']['operation'] == 'select_tools' and event['dialog']['names'] == ['selected']
        assert 'excluded' in event['dialog']['current_tools']['active']
        assert 'excluded' in {tool.name for tool in owner.agent.active_tools}
        response = {'id': event['dialog']['id'], 'request_id': event['request_id'],
                    'session_id': event['session_id'], 'scope': event['scope'], 'value': ending != 'deny'}
        with pytest.raises(ValueError, match='owner'):
            controller.answer({**response, 'scope': 'foreign'})
        if ending == 'withdraw':
            controller.negotiate({'extension_active_tools': 0})
        else:
            if ending == 'changed':
                owner.agent.set_tools((*owner.agent.state.tools, AgentTool(name='new', parameters={})))
            controller.answer(response)
        await controller.task
        await asyncio.sleep(0)
        if ending == 'apply':
            assert [tool.name for tool in owner.agent.active_tools] == ['selected']
            completed = next(row for row in events if row['type'] == 'extension_active_tools_result')
            assert completed['applied']
            result = next(row for row in events if row['type'] == 'extension_result')
            assert events.index(result) < events.index(completed)
            payload = json.loads(result['output'].removeprefix('TOOLS-QUEUED '))
            assert payload['before']['active'] == payload['after']['active']
        else:
            assert 'excluded' in {tool.name for tool in owner.agent.active_tools}
            assert not any(row.get('applied') for row in events if row['type'] == 'extension_active_tools_result')
        assert 'excluded' in {tool.name for tool in owner.agent.state.tools}
        assert owner.permissions.mode == 'deny' and owner.agent.messages is history and not calls
    finally:
        await controller.aclose()
        await navigation.aclose()
        await close(owner)


def test_actual_cli_tool_selection_review_then_application_receipt(tmp_path):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/toolselector', root / 'toolselector')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['toolselector'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_active_tools': 1})
        caps = peer.read('rpc_capabilities')
        assert caps['extension_active_tools'] == 1 and caps['extension_navigation'] == 0
        peer.send({'type': 'extension_control', 'request_id': 'select', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-toolselector-select', 'args': '{"names":[]}'})
        event = peer.read('extension_dialog')
        assert event['dialog']['operation'] == 'select_tools' and event['dialog']['names'] == []
        assert event['dialog']['current_tools']['active']
        peer.send({'type': 'extension_dialog_response', 'id': event['dialog']['id'],
                   'request_id': event['request_id'], 'session_id': event['session_id'],
                   'scope': event['scope'], 'value': True})
        result = peer.read('extension_result')
        applied = peer.read('extension_active_tools_result')
        assert result['success'] and applied['applied'] and peer.events.index(result) < peer.events.index(applied)
        # Commands are not model tools; inspect the now-empty selection through
        # the same explicit command without invoking a provider/tool loop.
        peer.send({'type': 'extension_control', 'request_id': 'again', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-toolselector-select', 'args': '{"names":[]}'} )
        second = peer.read('extension_dialog')
        assert second['dialog']['current_tools']['active'] == []
        peer.send({'type': 'extension_dialog_response', 'id': second['dialog']['id'],
                   'request_id': second['request_id'], 'session_id': second['session_id'],
                   'scope': second['scope'], 'value': False})
        assert peer.read('extension_result')['success']
        assert not peer.marker.exists()
        peer.send({'type': 'quit'})
        peer.read('quit_ack')
        assert peer.process.wait(timeout=6) == 0
    finally:
        peer.close()
