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


@pytest.mark.parametrize('command', ['ask', 'form', 'many', 'timeout'])
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


async def test_remote_replies_cannot_cross_dialog_owner_or_answer_tool_approval(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    controller.negotiate({'extension_dialogs': 1})
    controller.start(packet(owner, 'owned', 'ext-dialogue-ask'), turn_running=False)
    async def wait(kind):
        async with asyncio.timeout(5):
            while not any(event['type'] == kind for event in events):
                await asyncio.sleep(0.003)
    try:
        await wait('extension_dialog')
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        for extra in ({'session_id': 'foreign'}, {'request_id': 'other'}, {'scope': 'retired'},
                      {'id': 'permission-id'}):
            with pytest.raises(ValueError):
                controller.answer(response(dialog, 'Blue', **extra))
        with pytest.raises(ValueError):
            controller.answer(response(dialog, 'Unknown choice'))
        assert not owner.permissions.resolve(dialog['dialog']['id'], 'allow_once')
        assert controller.busy
        with pytest.raises(RuntimeError):
            controller.start(packet(owner, 'overlap', 'ext-dialogue-ask'), turn_running=False)
        controller.answer(response(dialog, 'Blue'))
        with pytest.raises(ValueError):
            controller.answer(response(dialog, 'Red'))
        controller.cancel({'request_id': 'owned', 'session_id': owner.session_id})
        await controller.task
        assert not controller.busy and not calls and not owner.extension_dialogs.has_pending
        assert owner.permissions.mode == 'deny'
        with pytest.raises(ValueError):
            controller.start(packet(owner, 'owned', 'ext-dialogue-ask'), turn_running=False)
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('hello', [{}, {'extension_dialogs': 1}, {'extension_dialogs': 1, 'extension_forms': 1}])
async def test_old_viewers_do_not_receive_unsupported_forms_or_fabricated_defaults(tmp_path, hello):
    owner, calls = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    controller.negotiate(hello)
    try:
        controller.start(packet(owner, 'unsupported', 'ext-dialogue-many'), turn_running=False)
        await controller.task
        assert not any(event['type'] == 'extension_dialog' for event in events)
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['success'] and result['output'] == 'MANY-RESULT-null' and not calls
    finally:
        await controller.aclose()
        await close(owner)


async def test_capability_withdrawal_cancels_pending_questions_and_late_answers_are_unavailable(tmp_path):
    owner, calls = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    controller.negotiate({'extension_dialogs': 1, 'extension_forms': 1})
    try:
        controller.start(packet(owner, 'question', 'ext-dialogue-form'), turn_running=False)
        async with asyncio.timeout(5):
            while not any(event['type'] == 'extension_dialog' for event in events):
                await asyncio.sleep(0.003)
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        controller.negotiate({'extension_dialogs': 0})
        await controller.task
        with pytest.raises(ValueError):
            controller.answer(response(dialog, {'name': 'LATE'}))
        assert 'FORM-RESULT-null' in next(event['output'] for event in events if event['type'] == 'extension_result')
        assert not calls and owner.extensions.dialog_handler is None
    finally:
        await controller.aclose()
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


async def test_rpc_prompt_tool_dialog_answer_is_separate_from_actual_permission_response(tmp_path):
    from xdog.ai.types import TextContent, ToolCall

    owner, _ = session(tmp_path)
    owner.permissions.set_mode('ask')
    models = []
    def stream(model, context, options=None):
        models.append(context)
        if context.messages[-1].role == 'user':
            alias = next(tool.name for tool in context.tools if tool.name.startswith('ext_dialogue_ask'))
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='ask', name=alias, arguments={}),),
                                                     stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RPC-PROMPT-DIALOG-DONE'),)))
    owner.agent.set_stream_fn(stream)
    pending = queue.Queue()
    pending.put({'type': 'rpc_hello', 'extension_dialogs': 1})
    pending.put({'type': 'prompt', 'request_id': 'prompt-owner', 'session_id': owner.session_id, 'message': 'Ask tool'})
    events, permission_ids = [], []
    class Input:
        def readline(self, size=-1):
            return json.dumps(pending.get(timeout=5)) + '\n'
    class Output:
        def write(self, text):
            for line in text.splitlines():
                if not line:
                    continue
                event = json.loads(line)
                events.append(event)
                if event['type'] == 'permission_request':
                    permission_ids.append(event['id'])
                    pending.put({'type': 'extension_dialog_response', 'id': event['id'],
                        'request_id': 'prompt-owner', 'session_id': owner.session_id,
                        'scope': 'guessed', 'value': True})
                elif event['type'] == 'error' and event.get('action') == 'extension_dialog_response':
                    assert not any(row['type'] == 'extension_dialog' for row in events)
                    assert permission_ids[-1] in owner.permissions._pending
                    pending.put({'type': 'permission_response', 'id': permission_ids[-1], 'decision': 'allow_once'})
                elif event['type'] == 'extension_dialog':
                    assert event['dialog']['kind'] == 'input'
                    assert event['request_id'] == 'prompt-owner'
                    pending.put(response(event, 'RPC-TOOL-INPUT'))
                elif event['type'] == 'message_end':
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
        assert len(models) == 2 and 'TOOL-DIALOG-RPC-TOOL-INPUT' in str(owner.agent.state.messages)
        assert any(event['type'] == 'extension_dialog_ack' for event in events)
        assert not owner.permissions._pending and owner.permissions.mode == 'ask'
        assert owner.extensions.dialog_handler is None and not owner.extension_dialogs.has_pending
    finally:
        await close(owner)


@pytest.mark.parametrize('ending', ['eof', 'cancel', 'quit'])
async def test_pending_rpc_command_question_stdin_end_or_owned_cancel_settles_worker(tmp_path, ending):
    owner, calls = session(tmp_path)
    pending = queue.Queue()
    pending.put({'type': 'rpc_hello', 'extension_dialogs': 1})
    pending.put(packet(owner, 'pending', 'ext-dialogue-ask'))
    events, processes = [], []
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
                    processes.append(owner.extensions._manager._clients['dialogue'].process)
                    pending.put(None if ending == 'eof' else {'type': 'quit'} if ending == 'quit' else {
                        'type': 'extension_control_cancel', 'request_id': event['request_id'],
                        'session_id': event['session_id']})
                elif ending == 'cancel' and event['type'] == 'extension_result':
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
        assert processes and all(process.returncode is not None for process in processes)
        assert not calls and not owner.extension_dialogs.has_pending and owner.extensions.dialog_handler is None
        if ending == 'cancel':
            assert next(event for event in events if event['type'] == 'extension_result')['canceled']
    finally:
        await close(owner)


@pytest.mark.parametrize('mode', ['ask', 'many', 'disconnect'])
def test_actual_cli_rpc_dialogs_and_disconnect_use_existing_worker_without_model_work(tmp_path, mode):
    import os
    import subprocess
    import sys
    import threading
    import time

    root, home = tmp_path / 'extensions', tmp_path / 'home'
    root.mkdir()
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/dialogue', root / 'dialogue')
    pid_marker = tmp_path / 'worker-pid'
    source = root / 'dialogue/main.py'
    source.write_text(source.read_text().replace('def setup(api):\n',
        'def setup(api):\n    from pathlib import Path\n    import os\n'
        '    Path(api.settings["pid_marker"]).write_text(str(os.getpid()))\n', 1))
    (home / 'settings.json').write_text(json.dumps({'extensions': ['dialogue'], 'extension_dir': str(root),
        'extension_settings': {'dialogue': {'pid_marker': str(pid_marker)}}}))
    fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
    marker = tmp_path / 'model'
    env = {**os.environ, 'CODING_DIR': str(home), 'XDOG_RPC_MARKER': str(marker)}
    process = subprocess.Popen([sys.executable, str(fixture)], cwd=tmp_path, env=env, text=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    incoming = queue.Queue()
    def collect():
        for line in process.stdout:
            incoming.put(json.loads(line))
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    def read(kind):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            event = incoming.get(timeout=max(0.1, deadline - time.monotonic()))
            if event['type'] == kind:
                return event
        raise AssertionError('Missing RPC event ' + kind)
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()
    try:
        ready = read('ready')
        assert ready['permission_mode'] == 'deny' and ready['capabilities']['extension_dialogs'] == 1
        send({'type': 'rpc_hello', 'extension_dialogs': 1, 'extension_forms': 1, 'extension_multi_forms': 1})
        read('rpc_capabilities')
        command = 'ask' if mode == 'disconnect' else mode
        send({'type': 'extension_control', 'request_id': 'actual', 'session_id': ready['session_id'],
              'action': 'command', 'name': 'ext-dialogue-' + command})
        if mode == 'disconnect':
            read('extension_dialog')
            process.stdin.close()
        elif mode == 'ask':
            for expected, answer in [('select', 'Blue'), ('input', 'ACTUAL-RPC-NAME'), ('confirm', True)]:
                event = read('extension_dialog')
                assert event['dialog']['kind'] == expected
                send(response(event, answer))
            result = read('extension_result')
            assert result['success'] and result['output'] == 'DIALOG-RESULT-Blue-ACTUAL-RPC-NAME-True'
            send({'type': 'quit'})
        else:
            event = read('extension_dialog')
            assert event['dialog']['kind'] == 'form'
            send(response(event, {'values': [False, 0, None], 'empty': []}))
            result = read('extension_result')
            assert result['success']
            assert json.loads(result['output'].removeprefix('MANY-RESULT-'))['values'] == [False, 0, None]
            send({'type': 'quit'})
        assert process.wait(timeout=6) == 0, process.stderr.read()
        thread.join(3)
        assert not thread.is_alive() and not marker.exists()
        worker_pid = int(pid_marker.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)  # Observes reaping; never signals or kills another user's process.
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        thread.join(3)
        if pid_marker.exists() and sys.platform.startswith('linux'):
            # Failure-path hygiene only: retain the assertion above, but don't
            # leave our own fixture worker alive after a failed investigation.
            worker_pid = int(pid_marker.read_text())
            command_line = Path(f'/proc/{worker_pid}/cmdline')
            try:
                argv = command_line.read_bytes()
                if b'xdog.agent.extensions.worker' in argv and str(root / 'dialogue').encode() in argv:
                    import signal
                    os.kill(worker_pid, signal.SIGTERM)
            except (FileNotFoundError, ProcessLookupError):
                pass


async def test_rpc_host_metadata_comes_from_owner_without_remote_terminal_navigation_authority(tmp_path):
    owner, calls = session(tmp_path, extra=('navigator',))
    events = []
    controller = RpcExtensions(owner, events.append)
    controller.negotiate({'extension_dialogs': 1})
    try:
        controller.start(packet(owner, 'inspect', 'ext-navigator-inspect'), turn_running=False)
        await controller.task
        result = next(event for event in events if event['type'] == 'extension_result')
        parsed = json.loads(result['output'].removeprefix('SESSION-INSPECTION '))
        assert parsed['model']['id'] == 'offline/model' and parsed['usage']['session_id'] == owner.session_id
        assert not any(event['type'] == 'extension_dialog' for event in events)
        events.clear()
        controller.start(packet(owner, 'browser', 'ext-navigator-browse'), turn_running=False)
        await controller.task
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['output'] == 'NAVIGATION-QUEUED-False' and not calls
        assert len(owner.session_manager.list_sessions()) == 1 and owner.permissions.mode == 'deny'
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
