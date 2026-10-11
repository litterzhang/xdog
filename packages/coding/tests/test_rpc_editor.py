"""Negotiated RPC editor requests reach a real client-owned draft CAS, never a host mirror/model turn."""
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
from xdog.tui.components.input_panel import InputPanel, _sanitize_editor_text
from xdog.tui.editor_actions import EditorActions


class Theme:
    accent = inverse = bold = dim = border = staticmethod(lambda text: text)


def setup(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/editor', root / 'editor')
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['editor'], 'extension_dir': str(root)}))
    manager = SessionManager(tmp_path / 'sessions')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    owner = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
        session_data=manager.create_session(working_dir=tmp_path, model='offline/model'), session_manager=manager,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    return owner, calls


async def close(owner):
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def respond(event, value):
    return {'type': 'extension_dialog_response', 'id': event['dialog']['id'], 'value': value,
            'session_id': event['session_id'], 'request_id': event['request_id'], 'scope': event['scope']}


def event_owner(event):
    return event['session_id'] + ':' + event['request_id'] + ':' + event['scope']


@pytest.mark.parametrize('conflict', ['none', 'text', 'cursor', 'image', 'owner'])
async def test_real_rpc_editor_client_cas_preserves_attachments_and_does_not_submit_or_approve(tmp_path, conflict):
    owner, calls = setup(tmp_path)
    editor = InputPanel(Theme())
    editor.set_text('CLIENT-DRAFT')
    images = ['kept-image']
    submissions = []
    editor.on_submit = submissions.append
    def replace(value):
        if _sanitize_editor_text(value) != value:
            return False
        editor.set_text(value)
        return True
    actions = EditorActions(lambda: (editor.get_text(), editor._cursor, tuple(images)), replace)
    pending = queue.Queue()
    pending.put({'type': 'rpc_hello', 'extension_editor': 1})  # Independent of basic dialogs/forms.
    pending.put({'type': 'extension_control', 'request_id': 'compose', 'session_id': owner.session_id,
                 'action': 'command', 'name': 'ext-editor-compose'})
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
                if event['type'] == 'extension_dialog':
                    assert event['dialog']['kind'] == 'editor'
                    scope = event_owner(event)
                    if event['dialog']['operation'] == 'replace':
                        if conflict == 'text':
                            editor.set_text('NEW-USER-DRAFT')
                        elif conflict == 'cursor':
                            editor._cursor = 0
                        elif conflict == 'image':
                            images.append('new-user-image')
                        elif conflict == 'owner':
                            scope = 'REPLACED-CLIENT-VIEW'
                    value = actions.handle(scope, event['dialog'])
                    pending.put(respond(event, value))
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
        assert result['success'] and result['output'] == 'EDITOR-APPLIED-' + str(conflict == 'none')
        assert editor.get_text() == ('CLIENT-DRAFT EXTENSION-COMPOSED' if conflict == 'none' else
                                     'NEW-USER-DRAFT' if conflict == 'text' else 'CLIENT-DRAFT')
        assert images[0] == 'kept-image' and not submissions and not calls
        assert not owner.agent.state.messages and owner.permissions.mode == 'deny'
        assert owner.extensions.dialog_handler is None and not owner.extension_dialogs.has_pending
    finally:
        await close(owner)


@pytest.mark.parametrize('hello', [{}, {'extension_dialogs': 1}, {'extension_forms': 1}])
async def test_editor_request_does_not_borrow_unrelated_ui_capabilities(tmp_path, hello):
    owner, calls = setup(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    controller.negotiate(hello)
    try:
        controller.start({'request_id': 'legacy', 'session_id': owner.session_id,
            'action': 'command', 'name': 'ext-editor-compose'}, turn_running=False)
        await controller.task
        assert not any(event['type'] == 'extension_dialog' for event in events)
        assert next(event['output'] for event in events if event['type'] == 'extension_result') == 'EDITOR-UNAVAILABLE'
        assert not calls
    finally:
        await controller.aclose()
        await close(owner)


async def test_revoked_editor_capability_and_wrong_owner_receipts_cannot_apply_or_answer_a_later_question(tmp_path):
    owner, calls = setup(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    controller.negotiate({'extension_editor': 1})
    try:
        controller.start({'request_id': 'read', 'session_id': owner.session_id,
            'action': 'command', 'name': 'ext-editor-compose'}, turn_running=False)
        async with asyncio.timeout(5):
            while not any(event['type'] == 'extension_dialog' for event in events):
                await asyncio.sleep(0.003)
        event = next(event for event in events if event['type'] == 'extension_dialog')
        snapshot = {'text': 'DRAFT', 'cursor': 5, 'image_count': 1, 'revision': 'a' * 64}
        for extra in ({'scope': 'stale'}, {'session_id': 'foreign'}, {'request_id': 'different'}):
            with pytest.raises(ValueError):
                controller.answer({**respond(event, snapshot), **extra})
        with pytest.raises(ValueError):
            controller.answer(respond(event, {**snapshot, 'cursor': True}))
        controller.negotiate({'extension_editor': 0})
        await controller.task
        with pytest.raises(ValueError):
            controller.answer(respond(event, snapshot))
        assert not controller.editor_revisions and not calls
        assert next(event['output'] for event in events if event['type'] == 'extension_result') == 'EDITOR-UNAVAILABLE'
    finally:
        await controller.aclose()
        await close(owner)


async def test_host_refuses_uncaptured_editor_revision_and_tools_cannot_borrow_editor_authority(tmp_path):
    owner, calls = setup(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    controller.negotiate({'extension_editor': 1})
    try:
        controller.control = 'host-test', owner.session_id
        async with controller.bind('host-test'):
            result = await controller._ask('editor', {'kind': 'editor', 'id': 'uncaptured', 'title': 'Unsafe replace',
                'operation': 'replace', 'text': 'NOT-APPLIED', 'revision': '0' * 64})
            assert result['applied'] is False and not any(event['type'] == 'extension_dialog' for event in events)
        await owner.initialize_extensions()
        result = await owner.extensions.tools()[0].execute('tool', {})
        assert result.content[0].text == 'TOOL-EDITOR-None-False' and not calls
    finally:
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('conflict', [False, True])
def test_actual_rpc_cli_editor_requests_apply_only_through_client_cas_and_never_call_model(tmp_path, conflict):
    import os
    import subprocess
    import sys
    import threading
    import time

    root, home = tmp_path / 'extensions', tmp_path / 'home'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/editor', root / 'editor')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['editor'], 'extension_dir': str(root)}))
    fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
    marker = tmp_path / 'model'
    process = subprocess.Popen([sys.executable, str(fixture)], cwd=tmp_path, text=True,
        env={**os.environ, 'CODING_DIR': str(home), 'XDOG_RPC_MARKER': str(marker)},
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
        raise AssertionError('Missing ' + kind)
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()
    editor = InputPanel(Theme())
    editor.set_text('/quit')  # Replacement is draft data, never an automatic RPC quit/prompt.
    images = ('CLIENT-IMAGE-REF',)
    submitted = []
    editor.on_submit = submitted.append
    def replace(value):
        if _sanitize_editor_text(value) != value:
            return False
        editor.set_text(value)
        return True
    actions = EditorActions(lambda: (editor.get_text(), editor._cursor, images), replace)
    try:
        ready = read('ready')
        assert ready['capabilities']['extension_editor'] == 1 and ready['permission_mode'] == 'deny'
        send({'type': 'rpc_hello', 'extension_editor': 1})
        assert read('rpc_capabilities')['extension_editor'] == 1
        send({'type': 'extension_control', 'request_id': 'compose', 'session_id': ready['session_id'],
              'action': 'command', 'name': 'ext-editor-compose'})
        event = read('extension_dialog')
        assert event['dialog']['operation'] == 'read'
        send(respond(event, actions.handle(event_owner(event), event['dialog'])))
        event = read('extension_dialog')
        assert event['dialog']['operation'] == 'replace'
        if conflict:
            editor.set_text('NEW-HUMAN-DRAFT')
        send(respond(event, actions.handle(event_owner(event), event['dialog'])))
        result = read('extension_result')
        assert result['success'] and result['output'] == 'EDITOR-APPLIED-' + str(not conflict)
        assert editor.get_text() == ('NEW-HUMAN-DRAFT' if conflict else '/quit EXTENSION-COMPOSED')
        assert not submitted and images == ('CLIENT-IMAGE-REF',) and not marker.exists()
        send({'type': 'status'})
        status = read('status')
        assert not status['prompt_running'] and status['permission_mode'] == 'deny' and status['messages'] == 0
        send({'type': 'quit'})
        assert process.wait(timeout=6) == 0, process.stderr.read()
        thread.join(3)
        assert not thread.is_alive()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        thread.join(3)
