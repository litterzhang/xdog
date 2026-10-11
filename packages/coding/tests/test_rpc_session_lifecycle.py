"""RPC uses the canonical session turn; disconnected readers cannot orphan its producer."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig, AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, StartEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.rpc.rpc_mode import _handle_prompt, run_rpc_mode


def session(tmp_path, stream, *, extension=False, hold=False, tools=()):
    settings = tmp_path / 'settings.json'
    if extension:
        root = tmp_path / 'extensions'
        shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/rpclifecycle',
                        root / 'rpclifecycle')
        settings.write_text(json.dumps({'extensions': ['rpclifecycle'], 'extension_dir': str(root),
            'extension_settings': {'rpclifecycle': {'hold': hold, 'marker': str(tmp_path / 'hook-entered')}}}))
    store = SessionManager(tmp_path / 'sessions')
    return AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model'), tools=tools),
        session_data=store.create_session(working_dir=tmp_path, model='offline/model'), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('deny'))


async def close(owner):
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


async def test_rpc_adapts_actual_canonical_turn_lifecycle_tool_results_compaction_and_persistence(
    tmp_path, monkeypatch,
):
    contexts = []
    def stream(model, context, options=None):
        contexts.append(context)
        if context.messages[-1].role == 'user':
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='read', name='current_time', arguments={}),),
                                                     stop_reason='toolUse'))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RPC-CANONICAL-ANSWER'),)))
    async def execute(*args, **kwargs):
        return AgentToolResult(content=(TextContent(text='ACTUAL-READ-RESULT'),))
    tool = AgentTool(name='current_time', description='Offline read-only probe', parameters={}, execute=execute)
    owner = session(tmp_path, stream, extension=True, tools=(tool,))
    compactions = []
    compact = owner._maybe_compact
    async def observed_compact():
        compactions.append(True)
        await compact()
    monkeypatch.setattr(owner, '_maybe_compact', observed_compact)
    events = []
    before_listeners = len(owner.agent._listeners)
    try:
        await _handle_prompt(owner, 'Read-only canonical turn', events.append)
        phases = [row['data']['event'] for row in owner.extensions.entries.for_extension('rpclifecycle')]
        assert phases.count('session_start') == phases.count('turn_start') == phases.count('turn_end') == 1
        assert phases.count('tool_end') == 1 and phases.count('message_end') >= 2
        assert len(contexts) == 2 and compactions == [True]
        assert events[0]['type'] == 'message_start'
        assert events[-1] == {'type': 'message_end', 'content': [{'type': 'text', 'text': 'RPC-CANONICAL-ANSWER'}]}
        saved = owner.session_manager.load_session(owner.session_id)
        assert 'ACTUAL-READ-RESULT' in str(saved.messages) and saved.extension_entries
        assert len(owner.agent._listeners) == before_listeners and owner.permissions.mode == 'deny'
    finally:
        await close(owner)


@pytest.mark.parametrize('ending', ['eof', 'abort', 'quit'])
async def test_rpc_reader_ending_cancels_preturn_hooks_or_actual_producer_before_return(tmp_path, ending):
    started, stopped = threading.Event(), threading.Event()
    streams = []
    def stream(*args):
        streams.append(args)
        async def generate():
            started.set()
            try:
                yield StartEvent(partial=AssistantMessage())
                await asyncio.Event().wait()
            finally:
                stopped.set()
        return EventStream(generate())
    owner = session(tmp_path, stream)
    lines = []
    read_index = [0]
    interrupted = threading.Event()
    class Output:
        def write(self, text):
            lines.extend(json.loads(line) for line in text.splitlines() if line)
            if any(line.get('type') == 'error' and line.get('message') == 'Interrupted' for line in lines):
                interrupted.set()
            return len(text)
        def flush(self):
            pass
    class Input:
        def readline(self, size=-1):
            read_index[0] += 1
            if read_index[0] == 1:
                return json.dumps({'type': 'prompt', 'message': 'Hold owned offline turn'}) + '\n'
            if read_index[0] == 2:
                assert started.wait(3)
                return '' if ending == 'eof' else json.dumps({'type': ending}) + '\n'
            assert interrupted.wait(3)
            return ''
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            async with asyncio.timeout(5):
                assert await run_rpc_mode(owner) == 0
        assert len(streams) == 1 and stopped.is_set() and interrupted.is_set()
        assert not owner.agent.is_streaming and owner.agent._producer_task.done()
        assert not owner.permissions._pending and owner.permissions.mode == 'deny'
    finally:
        await close(owner)


async def test_rpc_abort_cancels_extension_preturn_work_even_before_agent_streaming(tmp_path):
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    owner = session(tmp_path, stream, extension=True, hold=True)
    events = []
    try:
        task = asyncio.create_task(_handle_prompt(owner, 'Blocked pre-turn hook', events.append))
        async with asyncio.timeout(3):
            while not (tmp_path / 'hook-entered').exists():
                await asyncio.sleep(0.003)
        process = owner.extensions._manager._clients['rpclifecycle'].process
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not calls and process.returncode is not None
        assert not owner.agent.is_streaming and owner.extensions.snapshot()['extensions'][0]['state'] == 'failed'
        assert events[-1] == {'type': 'error', 'message': 'Interrupted'}
    finally:
        await close(owner)


async def test_rpc_invalid_configuration_reports_error_and_keeps_reader_ready_for_status(tmp_path):
    owner = session(tmp_path, lambda *args: EventStream.empty(AssistantMessage()))
    packets = iter([{'type': 'set_model', 'model': 'invalid name'}, {'type': 'status'}, {'type': 'quit'}])
    events = []
    class Input:
        def readline(self, size=-1):
            return json.dumps(next(packets)) + '\n'
    class Output:
        def write(self, text):
            events.extend(json.loads(line) for line in text.splitlines() if line)
            return len(text)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            assert await run_rpc_mode(owner) == 0
        assert any(event.get('type') == 'error' and event.get('action') == 'set_model' for event in events)
        assert any(event.get('type') == 'status' and event['model'] == 'offline/model' for event in events)
        assert events[-1] == {'type': 'quit_ack'}
    finally:
        await close(owner)


@pytest.mark.parametrize('ending', ['eof', 'quit', 'abort'])
def test_actual_cli_jsonl_disconnect_cancel_settles_producer_and_process(tmp_path, ending):
    import os
    import queue
    import subprocess
    import sys
    import time

    fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
    marker = tmp_path / 'provider'
    env = {**os.environ, 'CODING_DIR': str(tmp_path / 'home'), 'XDOG_RPC_MARKER': str(marker)}
    process = subprocess.Popen([sys.executable, str(fixture)], cwd=tmp_path, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    emitted = queue.Queue()
    lines = []
    def read_output():
        for line in process.stdout:
            decoded = json.loads(line)
            lines.append(decoded)
            emitted.put(decoded)
    thread = threading.Thread(target=read_output, daemon=True)
    thread.start()
    def wait(kind):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            item = emitted.get(timeout=max(0.1, deadline - time.monotonic()))
            if item.get('type') == kind:
                return item
        raise AssertionError(f'Missing {kind} in {lines}')
    def send(packet):
        process.stdin.write(json.dumps(packet) + '\n')
        process.stdin.flush()
    try:
        ready = wait('ready')
        assert ready['permission_mode'] == 'deny'
        send({'type': 'prompt', 'message': 'hold owned offline provider'})
        wait('message_start')
        deadline = time.monotonic() + 6
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        if ending == 'eof':
            process.stdin.close()
        else:
            send({'type': ending})
            if ending == 'abort':
                assert wait('error')['message'] == 'Interrupted'
                send({'type': 'status'})
                status = wait('status')
                assert not status['prompt_running'] and status['permission_mode'] == 'deny'
                send({'type': 'prompt', 'message': 'Fresh offline successor'})
                message = wait('message_end')
                assert message['content'] == [{'type': 'text', 'text': 'RPC-OFFLINE-ANSWER'}]
                assert not message.get('is_error')
                send({'type': 'quit'})
        assert process.wait(timeout=6) == 0, process.stderr.read()
        thread.join(3)
        assert not thread.is_alive() and marker.with_suffix('.settled').read_text() == 'producer-settled'
        assert any(event.get('type') == 'error' and event.get('message') == 'Interrupted' for event in lines)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        thread.join(3)


async def test_rpc_partial_provider_failure_keeps_content_but_never_reports_success(tmp_path):
    def stream(*args):
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RETAINED-PARTIAL'),),
            stop_reason='error', error_message='OFFLINE-PROVIDER-ERROR'))
    owner = session(tmp_path, stream)
    events = []
    try:
        await _handle_prompt(owner, 'Offline provider failure', events.append)
        final = next(event for event in events if event['type'] == 'message_end')
        assert final == {'type': 'message_end', 'content': [{'type': 'text', 'text': 'RETAINED-PARTIAL'}],
                         'is_error': True}
        assert events[-1] == {'type': 'error', 'message': 'OFFLINE-PROVIDER-ERROR'}
        saved = owner.session_manager.load_session(owner.session_id)
        assert 'RETAINED-PARTIAL' in str(saved.messages)
        assert saved.messages[-1].error_message == 'OFFLINE-PROVIDER-ERROR'
    finally:
        await close(owner)
