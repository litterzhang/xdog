"""Reviewed extension navigation queues only on successful callback, then reuses the native staged owner."""
from __future__ import annotations

import asyncio
import json
import queue
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, Model, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.rpc_mode import run_rpc_mode


class Adapter:
    def __init__(self):
        self.calls = []
        self.profile = Model(id='offline/model', context_window=128000)
    def resolve_model(self, name):
        return self.profile if name == self.profile.id else None
    def models(self):
        return (self.profile,)
    def __call__(self, model, context, options):
        self.calls.append(context)
        return EventStream.empty(AssistantMessage())


def setup(tmp_path):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/navigator', root / 'navigator')
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['navigator'], 'extension_dir': str(root),
        'extension_settings': {'navigator': {'marker': str(tmp_path / 'queued'),
                                           'release': str(tmp_path / 'release')}}}))
    store = SessionManager(tmp_path / 'sessions')
    adapter = Adapter()
    owner = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/model')),
        session_data=store.create_session(working_dir=tmp_path, model='offline/model'), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        permissions=PermissionManager('deny'))
    owner.model_info = adapter.profile
    owner.agent.replace_messages([UserMessage(content='ORIGINAL'),
                                  AssistantMessage(content=(TextContent(text='answer'),))])
    owner._persist()
    return owner, store, adapter


async def close(owner):
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def response(event, value):
    return {'type': 'extension_dialog_response', 'id': event['dialog']['id'], 'value': value,
        'request_id': event['request_id'], 'session_id': event['session_id'], 'scope': event['scope']}


@pytest.mark.parametrize('action', ['new', 'resume', 'branch', 'goto', 'fork', 'deny', 'fail'])
async def test_actual_rpc_review_callback_success_then_native_navigation_and_no_hidden_approval(tmp_path, action):
    owner, store, adapter = setup(tmp_path)
    original = owner.session_id
    branch = owner.create_branch(at_index=1)
    owner.agent.append_message(UserMessage(content='LATER-TAIL'))
    owner._persist()
    target = store.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = [UserMessage(content='TARGET-RESTORE')]
    store.save_session(target)
    options = {'action': action if action in {'new', 'resume', 'branch', 'goto', 'fork'} else 'new'}
    if action == 'resume':
        options['session_id'] = target.session_id
    if action == 'branch':
        options['branch_id'] = branch
    if action in {'goto', 'fork'}:
        options['index'] = 0
    if action == 'fail':
        options['fail'] = True
    pending = queue.Queue()
    pending.put({'type': 'rpc_hello', 'extension_navigation': 1})
    pending.put({'type': 'extension_control', 'request_id': 'review', 'session_id': original,
        'action': 'command', 'name': 'ext-navigator-navigate', 'args': json.dumps(options)})
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
                    assert event['dialog']['operation'] == 'navigate'
                    assert owner.session_id == original and len(owner.messages) == 3
                    pending.put(response(event, action != 'deny'))
                elif event['type'] == 'extension_result':
                    if action == 'deny':
                        assert event['output'] == 'NAVIGATION-REVIEWED-False'
                        pending.put({'type': 'quit'})
                    elif action == 'fail':
                        assert not event['success']
                        pending.put({'type': 'quit'})
                    else:
                        assert event['success'] and event['output'] == 'NAVIGATION-REVIEWED-True'
                        assert owner.session_id == original  # QUEUED result is not activation/persistence receipt.
                elif event['type'] == 'session_result':
                    assert event['success'] and owner.permissions.mode == 'deny'
                    pending.put({'type': 'quit'})
            return len(text)
        def flush(self):
            pass
    try:
        with patch('xdog.coding.modes.rpc.rpc_mode.sys.stdin', Input()), patch(
            'xdog.coding.modes.rpc.rpc_mode.sys.stdout', Output(),
        ):
            async with asyncio.timeout(10):
                assert await run_rpc_mode(owner) == 0
        if action == 'new':
            assert owner.session_id != original and not owner.messages
        elif action == 'resume':
            assert owner.session_id == target.session_id and 'TARGET-RESTORE' in str(owner.messages)
        elif action == 'branch':
            assert owner.session_data.active_branch == branch and len(owner.messages) == 2
        elif action == 'goto':
            assert len(owner.messages) == 1
        elif action == 'fork':
            assert len(owner.session_data.branches) == 2 and len(owner.messages) == 3
        else:
            assert owner.session_id == original and len(owner.messages) == 3
        assert not adapter.calls and owner.permissions.mode == 'deny'
    finally:
        await close(owner)


@pytest.mark.parametrize('changed', ['capability', 'revision', 'cancel'])
async def test_reviewed_target_is_discarded_on_capability_revision_or_callback_cancel_before_native_admission(
    tmp_path, changed,
):
    owner, _, adapter = setup(tmp_path)
    original = owner.session_id
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_navigation': 1})
    controller.start({'action': 'command', 'name': 'ext-navigator-navigate', 'request_id': 'queued',
        'session_id': original, 'args': '{"action":"new","hold":true}'}, turn_running=False)
    try:
        async with asyncio.timeout(5):
            while not any(event['type'] == 'extension_dialog' for event in events):
                await asyncio.sleep(0.003)
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        controller.answer(response(dialog, True))
        async with asyncio.timeout(5):
            while not (tmp_path / 'queued').exists():
                await asyncio.sleep(0.003)
        assert owner.session_id == original and navigation.task is None
        if changed == 'capability':
            controller.negotiate({'extension_navigation': 0})
        elif changed == 'revision':
            navigation.refresh_revision()
        else:
            controller.cancel({'request_id': 'queued', 'session_id': original})
        (tmp_path / 'release').touch()
        await controller.task
        await asyncio.sleep(0)
        assert owner.session_id == original and navigation.task is None and not adapter.calls
    finally:
        await navigation.aclose()
        await controller.aclose()
        await close(owner)


async def test_unrelated_capabilities_do_not_grant_navigation_and_cross_target_operation_is_not_decomposed(tmp_path):
    owner, store, adapter = setup(tmp_path)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_dialogs': 1, 'extension_editor': 1})
    try:
        controller.start({'action': 'command', 'name': 'ext-navigator-navigate', 'request_id': 'old',
                          'session_id': owner.session_id, 'args': 'new'}, turn_running=False)
        await controller.task
        assert not any(event['type'] == 'extension_dialog' for event in events)
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['output'] == 'NAVIGATION-REVIEWED-False'
        target = store.create_session(model='offline/model', working_dir=tmp_path)
        controller.negotiate({'extension_navigation': 1})
        events.clear()
        controller.start({'action': 'command', 'name': 'ext-navigator-navigate', 'request_id': 'cross',
            'session_id': owner.session_id,
            'args': json.dumps({'action': 'branch', 'session_id': target.session_id, 'branch_id': 'unknown'})},
            turn_running=False)
        await controller.task
        assert not any(event['type'] == 'extension_dialog' for event in events) and navigation.task is None
        assert not adapter.calls
    finally:
        await navigation.aclose()
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('fail', [False, True])
@pytest.mark.parametrize('selection', ['new', 'crossgoto'])
def test_actual_cli_reviewed_extension_new_session_defers_until_callback_success_and_never_calls_model(
    tmp_path, fail, selection,
):
    import os
    import subprocess
    import sys
    import threading
    import time

    root, home = tmp_path / 'extensions', tmp_path / 'home'
    home.mkdir()
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/navigator', root / 'navigator')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['navigator'], 'extension_dir': str(root)}))
    store = SessionManager(home / 'sessions')
    target = store.create_session(model='rpc-fixture', working_dir=tmp_path)
    target.messages = [UserMessage(content='UNSELECTED-CLI-LIVE-HEAD')]
    from xdog.coding.core.messages import messages_to_dicts
    target.branches = [{'branch_id': 'cli-selected', 'branch_point': 1, 'model': 'rpc-fixture',
        'messages': messages_to_dicts([UserMessage(content='CLI-SELECTED-CONTEXT'), AssistantMessage()]),
        'settings': {}, 'plan': None, 'extension_entries': {}}]
    store.save_session(target)
    before_target = store._session_path(target.session_id).read_bytes()
    marker = tmp_path / 'model'
    fixture = Path(__file__).parent / 'fixtures/rpc_client.py'
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
    try:
        ready = read('ready')
        assert ready['capabilities']['extension_navigation'] == 1
        send({'type': 'rpc_hello', 'extension_navigation': 1})
        assert read('rpc_capabilities')['extension_navigation'] == 1
        options = ({'action': 'new', 'fail': fail} if selection == 'new' else
                   {'action': 'goto', 'session_id': target.session_id, 'branch_id': 'cli-selected',
                    'index': 0, 'fail': fail})
        send({'type': 'extension_control', 'request_id': 'review', 'session_id': ready['session_id'],
              'name': 'ext-navigator-navigate', 'action': 'command', 'args': json.dumps(options)})
        question = read('extension_dialog')
        assert question['dialog']['target'] == ({'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}
            if selection == 'new' else {key: value for key, value in options.items() if key != 'fail'})
        assert question['session_id'] == ready['session_id']
        send(response(question, True))
        result = read('extension_result')
        assert result['success'] is not fail
        if not fail:
            queued = read('extension_navigation_queued')
            committed = read('session_result')
            assert committed['request_id'] == queued['navigation_request_id']
            assert committed['success'] and committed['session_id'] != ready['session_id']
            if selection == 'crossgoto':
                assert committed['session_id'] == target.session_id
                selected = store.load_session(target.session_id)
                assert len(selected.messages) == 1 and 'CLI-SELECTED-CONTEXT' in str(selected.messages)
                assert 'UNSELECTED-CLI-LIVE-HEAD' in str(selected.branches[-1]['messages'])
        else:
            discarded = read('extension_navigation_discarded')
            assert discarded['request_id'] == 'review'
            send({'type': 'status'})
            assert read('status')['session_id'] == ready['session_id']
            assert store._session_path(target.session_id).read_bytes() == before_target
        assert not marker.exists()
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


@pytest.mark.parametrize('kind', ['navigation', 'editor'])
async def test_transport_answer_that_loses_to_a_completed_canceled_broker_future_cannot_create_host_grant(
    tmp_path, kind,
):
    owner, _, adapter = setup(tmp_path)
    events = []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_navigation': 1, 'extension_editor': 1})
    controller.control = 'race', owner.session_id
    try:
        async with controller.bind('race'):
            if kind == 'navigation':
                request = {'kind': 'session', 'id': 'worker', 'title': 'Review target', 'operation': 'navigate',
                    'target': {'action': 'new', 'session_id': '', 'branch_id': '', 'index': -1}}
                proposed = True
            else:
                request = {'kind': 'editor', 'id': 'worker', 'title': 'Read editor', 'operation': 'read'}
                proposed = {'text': 'OLD-DRAFT', 'cursor': 9, 'image_count': 0, 'revision': 'a' * 64}
            waiting = asyncio.create_task(controller._ask('navigator', request))
            async with asyncio.timeout(5):
                while not any(event['type'] == 'extension_dialog' for event in events):
                    await asyncio.sleep(0.003)
            event = next(event for event in events if event['type'] == 'extension_dialog')
            # Real broker future already resolved as cancellation, but its coroutine
            # has not yet removed the ID. A transport ACK is not the callback's value.
            future = owner.extension_dialogs._pending[event['dialog']['id']][2]
            future.set_result(None)
            with pytest.raises(ValueError, match='owns input'):
                controller.answer(response(event, proposed))
            assert await waiting is None
            assert controller.deferred_navigation is None and not controller.editor_revisions
            assert navigation.task is None and not adapter.calls
    finally:
        await navigation.aclose()
        await controller.aclose()
        await close(owner)


@pytest.mark.parametrize('action', ['branch', 'goto'])
@pytest.mark.parametrize('outcome', ['accept', 'deny', 'fail'])
async def test_reviewed_cross_session_branch_checkpoint_uses_one_native_commit_not_resume_then_restore(
    tmp_path, action, outcome, monkeypatch,
):
    owner, store, adapter = setup(tmp_path)
    original = owner.session_id
    saved = owner.create_branch(at_index=0)
    target = store.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = [UserMessage(content='MUST-NOT-FIRST-ADOPT-TARGET-HEAD')]
    target.branches = [dict(next(branch for branch in owner.session_data.branches if branch['branch_id'] == saved))]
    store.save_session(target)
    before = store._session_path(target.session_id).read_bytes()
    events, commits = [], []
    navigation = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=navigation)
    controller.negotiate({'extension_navigation': 1})
    original_save = store.save_session
    def observe(data):
        if data.session_id == target.session_id:
            commits.append((owner.session_id, list(data.messages)))
        original_save(data)
    monkeypatch.setattr(store, 'save_session', observe)
    def no_intermediate(*args):
        raise AssertionError('Reviewed selection cannot first activate live target head')
    monkeypatch.setattr(owner, 'switch_session', no_intermediate)
    monkeypatch.setattr(owner, 'restore_branch', no_intermediate)
    try:
        options = {'action': action, 'session_id': target.session_id, 'branch_id': saved,
                   'fail': outcome == 'fail', **({'index': 0} if action == 'goto' else {})}
        controller.start({'action': 'command', 'name': 'ext-navigator-navigate', 'request_id': 'cross-review',
                          'session_id': original, 'args': json.dumps(options)}, turn_running=False)
        async with asyncio.timeout(5):
            while not any(event['type'] == 'extension_dialog' for event in events):
                await asyncio.sleep(0.003)
        question = next(event for event in events if event['type'] == 'extension_dialog')
        assert question['dialog']['target']['session_id'] == target.session_id
        assert question['dialog']['target']['branch_id'] == saved
        assert owner.session_id == original and not commits
        controller.answer(response(question, outcome != 'deny'))
        await controller.task
        if navigation.task is not None:
            await navigation.task
        if outcome == 'accept':
            assert owner.session_id == target.session_id and len(commits) == 1 and commits[0][0] == original
            assert len(owner.messages) == 1 and 'ORIGINAL' in str(owner.messages)
            assert events[-1]['type'] == 'session_result' and events[-1]['success']
        else:
            assert owner.session_id == original and not commits
            assert store._session_path(target.session_id).read_bytes() == before
        assert not adapter.calls and owner.permissions.mode == 'deny'
    finally:
        await navigation.aclose()
        await controller.aclose()
        await close(owner)
