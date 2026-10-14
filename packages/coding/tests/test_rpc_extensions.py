"""Actual process workers, command/turn brokers and JSONL reader with separate permission authority."""
from __future__ import annotations

import asyncio
import json
import queue
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode


def session(tmp_path, *, extra=()):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/dialogue', root / 'dialogue')
    for name in extra:
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions' / name, root / name)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['dialogue', *extra], 'extension_dir': str(root)}))
    manager = SessionManager(tmp_path / 'sessions')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    owner = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('deny'))
    return owner, calls


async def close(owner):
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def packet(owner, request_id, name, *, action='command'):
    return {'type': 'extension_control', 'request_id': request_id, 'session_id': owner.session_id,
            'action': action, 'name': name}


def response(event, value, **extra):
    return {'type': 'extension_dialog_response', 'id': event['dialog']['id'], 'request_id': event['request_id'],
            'session_id': event['session_id'], 'scope': event['scope'], 'value': value, **extra}


@pytest.mark.parametrize('command', ['ask'])
async def test_actual_rpc_reader_negotiated_questions_forms_scoped_answers_and_no_model_or_permission_change(
    tmp_path, command,
):
    owner, calls = session(tmp_path)
    messages = owner.agent.state.messages
    pending = queue.Queue()
    pending.put({'type': 'rpc_hello', 'extension_dialogs': 1, 'extension_forms': 1, 'extension_multi_forms': 1})
    pending.put(packet(owner, 'owned', 'ext-dialogue-' + command))
    events = []
    class Input:
        def readline(self, size=-1):
            value = pending.get(timeout=5)
            return '' if value is None else json.dumps(value) + '\n'
    class Output:
        def write(self, text):
            for line in text.splitlines():
                if not line:
                    continue
                event = json.loads(line)
                events.append(event)
                if event['type'] == 'extension_dialog':
                    kind = event['dialog']['kind']
                    if command == 'timeout':
                        continue  # Actual broker deadline, no automatic answer/default.
                    answer = {'select': 'Blue', 'input': 'RPC-NAME', 'confirm': True}.get(kind)
                    if command == 'form':
                        answer = {'name': 'RPC-FORM', 'count': 0, 'enabled': False, 'mode': None, 'notes': 'line\nnext'}
                    elif command == 'many':
                        answer = {'values': [False, 0, None], 'empty': []}
                    pending.put(response(event, answer))
                elif event['type'] == 'extension_result':
                    pending.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            async with asyncio.timeout(8):
                assert await run_rpc_mode(owner) == 0
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['success'] and not calls and owner.agent.state.messages is messages
        assert owner.permissions.mode == 'deny' and not owner.permissions._pending
        if command == 'ask':
            assert result['output'] == 'DIALOG-RESULT-Blue-RPC-NAME-True'
            assert [event['dialog']['kind'] for event in events if event['type'] == 'extension_dialog'] == [
                'select', 'input', 'confirm']
        elif command == 'form':
            parsed = json.loads(result['output'].removeprefix('FORM-RESULT-'))
            assert parsed['count'] == 0 and parsed['enabled'] is False and parsed['mode'] is None
        elif command == 'many':
            parsed = json.loads(result['output'].removeprefix('MANY-RESULT-'))
            assert parsed['values'] == [False, 0, None] and parsed['empty'] == []
        else:
            assert result['output'] == 'TIMEOUT-None'
        assert owner.extensions.dialog_handler is None and not owner.extension_dialogs.has_pending
    finally:
        await close(owner)


async def test_early_cancel_and_stdin_close_do_not_admit_worker_or_replay_a_request(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.start(packet(owner, 'early', 'ext-dialogue-ask'), turn_running=False)
        controller.cancel({'request_id': 'early', 'session_id': owner.session_id})
        await asyncio.gather(controller.task, return_exceptions=True)
        await asyncio.sleep(0)
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['canceled'] and not result['success'] and not calls
        assert not owner.extensions._infos and not controller.busy
    finally:
        await controller.aclose()
        await close(owner)


async def test_boolean_capabilities_and_malformed_control_do_not_admit_callbacks(tmp_path):
    owner, calls = session(tmp_path)
    controller = RpcExtensions(owner, lambda event: None)
    try:
        for name in ('extension_dialogs', 'extension_forms', 'extension_multi_forms'):
            with pytest.raises(ValueError):
                controller.negotiate({name: True})
        for bad in ({'action': []}, {'args': []}, {'request_id': True}, {'session_id': 'foreign'},
                    {'args': 'x' * (64 * 1024 + 1)}):
            with pytest.raises(ValueError):
                controller.start({**packet(owner, 'safe', 'ext-dialogue-ask'), **bad}, turn_running=False)
        assert not controller.busy and not controller.used and not owner.extensions._infos and not calls
    finally:
        await controller.aclose()
        await close(owner)
