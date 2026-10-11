"""Typed RPC attachments use existing model limits, context/persistence and owned cancellation."""
from __future__ import annotations

import asyncio
import json
import queue
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent, VisionLimits
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.rpc.rpc_mode import _handle_prompt, _prompt_images, run_rpc_mode

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


def owner(tmp_path, *, vision=True, count=4, size=1024, media=('image/png',)):
    calls = []
    def stream(model, context, options=None):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RPC-IMAGE-ANSWER'),)))
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
        session_data=manager.create_session(model='offline/model', working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    session.model_info = Model(id='offline/model', input=('text', 'image') if vision else ('text',),
        vision_limits=VisionLimits(max_prompt_images=count, max_prompt_image_size=size, supported_media_types=media))
    return session, calls


async def close(session):
    await session.children.supervisor.aclose()
    await session.extensions.aclose()
    session.dispose()


@pytest.mark.parametrize('invalid', [None, {}, ['image'], [1], [{'data': PNG}], [{'data': PNG, 'mime_type': False}],
    [{'data': PNG, 'mime_type': 'image/gif'}], [{'type': 'text', 'data': PNG, 'mime_type': 'image/png'}],
    [{'data': PNG, 'mime_type': 'image/png', 'path': '/do/not/read'}],
    [{'uri': 'https://do-not-fetch.invalid/image.png'}], [{'data': PNG, 'mime_type': 'image/png'}] * 5])
def test_rpc_attachments_refuse_nonimage_shapes_and_implicit_uri_or_file_resolution(invalid):
    with pytest.raises(ValueError):
        _prompt_images(invalid)


@pytest.mark.parametrize('text, negotiated', [('', False), ('Inspect the attached image', False), ('', True)])
async def test_actual_rpc_image_only_or_text_prompt_retains_typed_bytes_and_persistence(tmp_path, text, negotiated):
    session, calls = owner(tmp_path)
    pending = queue.Queue()
    if negotiated:
        pending.put({'type': 'rpc_hello', 'extension_dialogs': 1})
    pending.put({'type': 'prompt', 'message': text,
                 'images': [{'type': 'image', 'data': PNG, 'mime_type': 'image/png'}],
                 'request_id': 'image', 'session_id': session.session_id})
    events = []
    class Input:
        def readline(self, size=-1):
            return json.dumps(pending.get(timeout=5)) + '\n'
    class Output:
        def write(self, value):
            for line in value.splitlines():
                if line:
                    event = json.loads(line)
                    events.append(event)
                    if event['type'] == 'message_end':
                        pending.put({'type': 'quit'})
            return len(value)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            async with asyncio.timeout(5):
                assert await run_rpc_mode(session) == 0
        assert len(calls) == 1 and session.permissions.mode == 'deny'
        attached = [part for part in calls[0].messages[-1].content if isinstance(part, ImageContent)]
        assert attached == [ImageContent(data=PNG, mime_type='image/png')]
        saved = session.session_manager.load_session(session.session_id)
        retained = [part for part in saved.messages[0].content if isinstance(part, ImageContent)]
        assert retained == attached
        assert PNG not in json.dumps(events)  # Wire outcome never echoes the binary attachment as prose.
        assert events[0]['capabilities']['image_prompts'] == 1
    finally:
        await close(session)


@pytest.mark.parametrize('failure', ['text-only', 'bad-base64', 'mime-mismatch', 'count', 'size', 'media'])
async def test_model_or_binary_image_failure_is_explicit_and_preserves_history_without_model_calls(tmp_path, failure):
    session, calls = owner(tmp_path, vision=failure != 'text-only', count=1 if failure == 'count' else 4,
                           size=1 if failure == 'size' else 1024,
                           media=('image/jpeg',) if failure == 'media' else ('image/png',))
    data = 'not base64' if failure == 'bad-base64' else PNG
    images = (ImageContent(data=data, mime_type='image/jpeg' if failure == 'mime-mismatch' else 'image/png'),)
    if failure == 'count':
        images *= 2
    previous = session.agent.state.messages
    events = []
    try:
        await _handle_prompt(session, '', events.append, images=images)
        assert not calls and session.agent.state.messages is previous and session.permissions.mode == 'deny'
        assert events[-1]['type'] == 'error' and not any(event['type'] == 'message_end' for event in events)
    finally:
        await close(session)


async def test_rpc_invalid_image_request_leaves_reader_ready_for_a_later_text_prompt(tmp_path):
    session, calls = owner(tmp_path)
    pending = queue.Queue()
    pending.put({'type': 'prompt', 'message': '', 'images': [{'path': '/never-read'}]})
    events = []
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
                if event['type'] == 'error':
                    pending.put({'type': 'prompt', 'message': 'A later valid text prompt'})
                elif event['type'] == 'message_end':
                    pending.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            async with asyncio.timeout(5):
                assert await run_rpc_mode(session) == 0
        assert len(calls) == 1
        assert calls[0].messages[-1].content[0].text == 'A later valid text prompt'
        assert next(event for event in events if event['type'] == 'error')['action'] == 'prompt'
    finally:
        await close(session)


async def test_turn_start_profile_withdrawal_revalidates_image_admission_before_history_or_compaction(
    tmp_path, monkeypatch,
):
    session, calls = owner(tmp_path)
    before = session.agent.state.messages
    original = session.extensions.event
    async def changed(event, extra=None):
        if event == 'turn_start':
            session.model_info = Model(id='offline/model', input=('text',))
        return await original(event, extra)
    monkeypatch.setattr(session.extensions, 'event', changed)
    compactions = []
    async def compact():
        compactions.append(True)
    monkeypatch.setattr(session, '_maybe_compact', compact)
    events = []
    try:
        await _handle_prompt(session, 'Image becomes unsupported', events.append,
                             images=(ImageContent(data=PNG, mime_type='image/png'),))
        assert not calls and not compactions and session.agent.state.messages is before
        assert events[-1]['type'] == 'error' and 'image input' in events[-1]['message']
    finally:
        await close(session)


@pytest.mark.parametrize('vision', [False, True])
def test_actual_cli_rpc_typed_image_transport_never_drops_binary_or_falls_back_to_text(tmp_path, vision):
    import os
    import subprocess
    import sys
    import threading
    import time
    from pathlib import Path

    fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
    marker = tmp_path / 'model'
    home = tmp_path / 'home'
    env = {**os.environ, 'CODING_DIR': str(home), 'XDOG_RPC_MARKER': str(marker), 'XDOG_RPC_VISION': str(int(vision))}
    process = subprocess.Popen([sys.executable, str(fixture)], cwd=tmp_path, env=env, text=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    incoming = queue.Queue()
    outputs = []
    def collect():
        for line in process.stdout:
            event = json.loads(line)
            outputs.append(event)
            incoming.put(event)
    thread = threading.Thread(target=collect, daemon=True)
    thread.start()
    def read(kind):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            event = incoming.get(timeout=max(0.1, deadline - time.monotonic()))
            if event['type'] == kind:
                return event
        raise AssertionError('Missing ' + kind)
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()
    try:
        ready = read('ready')
        assert ready['capabilities']['image_prompts'] == 1
        send({'type': 'prompt', 'images': [{'data': PNG, 'mime_type': 'image/png'}]})
        if vision:
            final = read('message_end')
            assert final['content'] == [{'type': 'text', 'text': 'RPC-IMAGES-1'}]
            assert marker.exists()
        else:
            assert 'image input' in read('error')['message']
            assert not marker.exists()
        send({'type': 'quit'})
        assert process.wait(timeout=6) == 0, process.stderr.read()
        thread.join(3)
        assert not thread.is_alive() and PNG not in json.dumps(outputs)
        saved = SessionManager(home / 'sessions').get_most_recent()
        if vision:
            assert any(isinstance(part, ImageContent) and part.data == PNG for part in saved.messages[0].content)
        else:
            assert not saved.messages
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        thread.join(3)


async def test_image_validation_runs_off_reader_loop_and_abort_prevents_model_admission(tmp_path, monkeypatch):
    import threading

    from xdog.ai.utils import image_files

    session, calls = owner(tmp_path)
    original = image_files.validate_references
    entered, release = threading.Event(), threading.Event()
    def blocked(images, model):
        entered.set()
        assert release.wait(4)
        original(images, model)
    monkeypatch.setattr(image_files, 'validate_references', blocked)
    pending = queue.Queue()
    pending.put({'type': 'prompt', 'images': [{'data': PNG, 'mime_type': 'image/png'}]})
    events = []
    reads = [0]
    class Input:
        def readline(self, size=-1):
            reads[0] += 1
            if reads[0] == 2:
                assert entered.wait(3)
                return json.dumps({'type': 'status'}) + '\n'
            return json.dumps(pending.get(timeout=5)) + '\n'
    class Output:
        def write(self, text):
            for line in text.splitlines():
                if not line:
                    continue
                event = json.loads(line)
                events.append(event)
                if event['type'] == 'status':
                    assert event['prompt_running'] and not release.is_set()
                    pending.put({'type': 'abort'})
                elif event.get('type') == 'ok' and event.get('action') == 'abort':
                    release.set()
                elif event['type'] == 'error' and event.get('message') == 'Interrupted':
                    pending.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            async with asyncio.timeout(6):
                assert await run_rpc_mode(session) == 0
        assert not calls and not session.agent.state.messages and session.permissions.mode == 'deny'
    finally:
        release.set()
        await close(session)
