"""Native session metadata and staged restore run through the real SDK, not returned command text."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, Model, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
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
        return EventStream.empty(AssistantMessage(content=(TextContent(text='FRESH-HEAD-ANSWER'),)))


def setup(tmp_path):
    adapter = Adapter()
    manager = SessionManager(tmp_path / 'sessions')
    owner = AgentSession(agent=Agent(adapter, config=AgentConfig(model='offline/model')),
        session_data=manager.create_session(working_dir=tmp_path, model='offline/model'), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    owner.model_info = adapter.profile
    owner.agent.replace_messages([UserMessage(content='ORIGINAL-HEAD'),
                                  AssistantMessage(content=(TextContent(text='reply'),))])
    owner._persist()
    return owner, manager, adapter


async def close(owner):
    await owner.children.supervisor.aclose()
    await owner.extensions.aclose()
    owner.dispose()


def packet(owner, nav, action, key, **fields):
    return {'type': 'session_control', 'action': action, 'request_id': key, 'expected_session': owner.session_id,
            'session_revision': nav.revision, **fields}


async def finish(nav):
    await nav.task
    assert not nav.busy


async def test_actual_saved_session_list_tree_branch_goto_resume_name_and_new_keep_policy_and_no_model_work(tmp_path):
    owner, manager, adapter = setup(tmp_path)
    original = owner.session_id
    target = manager.create_session(model='offline/model', working_dir=tmp_path)
    target.name = 'TARGET-SEARCH-NAME'
    target.messages = [UserMessage(content='TARGET-HEAD'),
                       AssistantMessage(content=(TextContent(text='target reply'),))]
    manager.save_session(target)
    events = []
    nav = RpcNavigation(owner, events.append)
    try:
        nav.start(packet(owner, nav, 'list', 'list', query='TARGET-SEARCH-NAME'),
                  turn_running=False, extension_running=False)
        await finish(nav)
        assert events[-1]['catalog']['entries'][0]['session_id'] == target.session_id
        assert owner.session_id == original
        nav.start(packet(owner, nav, 'fork', 'fork', index=1), turn_running=False, extension_running=False)
        await finish(nav)
        saved_branch = events[-1]['result']
        owner.agent.append_message(UserMessage(content='LATER-TAIL'))
        owner._persist()
        nav.start(packet(owner, nav, 'tree', 'tree'), turn_running=False, extension_running=False)
        await finish(nav)
        assert any(row['branch_id'] == saved_branch for row in events[-1]['catalog']['entries'])
        nav.start(packet(owner, nav, 'branch', 'branch', branch_id=saved_branch),
                  turn_running=False, extension_running=False)
        await finish(nav)
        assert events[-1]['success'] and len(owner.messages) == 2
        nav.start(packet(owner, nav, 'goto', 'goto', index=0), turn_running=False, extension_running=False)
        await finish(nav)
        assert events[-1]['success'] and len(owner.messages) == 1
        nav.start(packet(owner, nav, 'resume', 'resume', target_session=target.session_id),
                  turn_running=False, extension_running=False)
        await finish(nav)
        assert owner.session_id == target.session_id and 'TARGET-HEAD' in str(owner.messages)
        assert events[-1]['success'] and nav.enforced
        nav.start(packet(owner, nav, 'rename', 'name', name='Renamed RPC session'),
                  turn_running=False, extension_running=False)
        await finish(nav)
        assert manager.load_session(target.session_id).name == 'Renamed RPC session'
        nav.start(packet(owner, nav, 'new', 'new'), turn_running=False, extension_running=False)
        await finish(nav)
        assert owner.session_id != target.session_id and not owner.messages and events[-1]['success']
        assert owner.permissions.mode == 'deny' and not adapter.calls
        with pytest.raises(ValueError):
            nav.prompt_owner({'session_id': original})
    finally:
        await nav.aclose()
        await close(owner)


async def test_fork_save_failure_does_not_publish_phantom_branch_or_mutate_persistence_owner(tmp_path, monkeypatch):
    owner, manager, _ = setup(tmp_path)
    original = owner.session_data
    before = manager._session_path(owner.session_id).read_bytes()
    plan, entries, old_update = owner.plan, owner.extensions.entries, original.updated_at
    def failed(value):
        raise OSError('offline branch write failure')
    monkeypatch.setattr(manager, '_write', failed)
    try:
        with pytest.raises(OSError):
            owner.create_branch(at_index=1)
        assert owner.session_data is original and not original.branches and original.updated_at == old_update
        assert owner.plan is plan and owner.extensions.entries is entries
        assert manager._session_path(owner.session_id).read_bytes() == before
    finally:
        monkeypatch.undo()
        await close(owner)


@pytest.mark.parametrize('when', ['unadmitted', 'committed'])
async def test_owned_cancel_keeps_reader_owner_until_actual_staged_navigation_settles(tmp_path, monkeypatch, when):
    owner, manager, _ = setup(tmp_path)
    original = owner.session_id
    target = manager.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = [UserMessage(content='TARGET-CANCEL-HEAD')]
    manager.save_session(target)
    entered, release = threading.Event(), threading.Event()
    if when == 'unadmitted':
        method = owner.switch_session
        def held(identifier):
            entered.set()
            assert release.wait(4)
            return method(identifier)
        monkeypatch.setattr(owner, 'switch_session', held)
    else:
        method = manager._write
        def held(data):
            method(data)
            if data.session_id == target.session_id:
                entered.set()
                assert release.wait(4)
        monkeypatch.setattr(manager, '_write', held)
    events = []
    nav = RpcNavigation(owner, events.append)
    try:
        nav.start(packet(owner, nav, 'resume', 'cancel', target_session=target.session_id),
                  turn_running=False, extension_running=False)
        async with asyncio.timeout(4):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        nav.cancel({'request_id': 'cancel', 'expected_session': original})
        nav.cancel({'request_id': 'cancel', 'expected_session': original})
        assert nav.busy
        release.set()
        await finish(nav)
        if when == 'unadmitted':
            assert owner.session_id == original and events[-1]['canceled']
        else:
            assert owner.session_id == target.session_id and events[-1]['success'] and nav.enforced
            assert events[-1]['receipt_canceled']
        assert owner.permissions.mode == 'deny'
    finally:
        release.set()
        await nav.aclose()
        await close(owner)


async def test_actual_rpc_reader_refuses_stale_post_navigation_prompt_until_current_revision_is_supplied(tmp_path):
    owner, manager, adapter = setup(tmp_path)
    original = owner.session_id
    target = manager.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = [UserMessage(content='TARGET-RPC-HEAD')]
    manager.save_session(target)
    pending = queue.Queue()
    events = []
    state = {}
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
                if event['type'] == 'ready':
                    state['old_revision'] = event['session_revision']
                    pending.put({'type': 'session_control', 'action': 'resume', 'request_id': 'resume',
                        'expected_session': original, 'target_session': target.session_id})
                elif event['type'] == 'session_result':
                    assert event['success']
                    state['revision'] = event['session_revision']
                    pending.put({'type': 'prompt', 'session_id': original, 'session_revision': state['old_revision'],
                                 'message': 'MUST-NOT-USE-OLD-CONTEXT'})
                elif event['type'] == 'error':
                    assert not adapter.calls
                    pending.put({'type': 'prompt', 'session_id': target.session_id,
                        'session_revision': state['revision'], 'message': 'Use actual target'})
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
        assert len(adapter.calls) == 1 and 'TARGET-RPC-HEAD' in str(adapter.calls[0].messages)
        assert 'MUST-NOT-USE-OLD-CONTEXT' not in str(owner.messages)
    finally:
        await close(owner)


async def test_navigation_refuses_busy_foreign_invalid_and_reused_operations_without_disk_changes(tmp_path):
    owner, manager, _ = setup(tmp_path)
    before = manager._session_path(owner.session_id).read_bytes()
    nav = RpcNavigation(owner, lambda event: None)
    try:
        for extra in ({'expected_session': 'foreign'}, {'index': True}, {'target_session': '../bad'},
                      {'branch_id': 'bad'}, {'target_session': 'other'}, {'action': []}):
            with pytest.raises(ValueError):
                nav.start({**packet(owner, nav, 'new', 'invalid'), **extra},
                          turn_running=False, extension_running=False)
        with pytest.raises(RuntimeError):
            nav.start(packet(owner, nav, 'new', 'busy'), turn_running=True, extension_running=False)
        assert manager._session_path(owner.session_id).read_bytes() == before and not nav.used
    finally:
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('action', ['resume', 'branch', 'goto'])
def test_actual_cli_rpc_named_session_discovery_staged_resume_and_revision_fence_without_model_work(tmp_path, action):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    home = tmp_path / 'home'
    manager = SessionManager(home / 'sessions')
    target = manager.create_session(model='rpc-fixture', working_dir=tmp_path)
    target.name = 'RPC-NAVIGATION-TARGET'
    from xdog.coding.core.messages import messages_to_dicts
    target.branches = [{'branch_id': 'cli-selected', 'branch_point': 1, 'model': 'rpc-fixture',
        'messages': messages_to_dicts([UserMessage(content='CLI-SELECTED-CONTEXT'), AssistantMessage()]),
        'settings': {}, 'plan': None, 'extension_entries': {}}]
    target.messages = [UserMessage(content='ACTUAL-SAVED-HEAD'),
                       AssistantMessage(content=(TextContent(text='Saved answer'),))]
    manager.save_session(target)
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
            item = incoming.get(timeout=max(0.1, deadline - time.monotonic()))
            if item['type'] == kind:
                return item
        raise AssertionError('Missing ' + kind)
    def send(value):
        process.stdin.write(json.dumps(value) + '\n')
        process.stdin.flush()
    try:
        ready = read('ready')
        assert ready['capabilities']['session_controls'] == 1
        assert ready['capabilities']['session_checkpoint_targets'] == 1
        origin = ready['session_id']
        send({'type': 'session_control', 'action': 'list', 'request_id': 'browse',
              'expected_session': origin, 'query': 'RPC-NAVIGATION-TARGET'})
        assert read('session_result')['catalog']['entries'][0]['session_id'] == target.session_id
        send({'type': 'session_control', 'action': action, 'request_id': 'select',
              'expected_session': origin, 'target_session': target.session_id,
              **({'branch_id': 'cli-selected'} if action in {'branch', 'goto'} else {}),
              **({'index': 0} if action == 'goto' else {})})
        activated = read('session_result')
        assert activated['success'] and activated['session_id'] == target.session_id
        if action != 'resume':
            selected = manager.load_session(target.session_id)
            assert 'CLI-SELECTED-CONTEXT' in str(selected.messages)
            assert len(selected.messages) == (1 if action == 'goto' else 2)
            assert 'ACTUAL-SAVED-HEAD' in str(selected.branches[-1]['messages'])
        send({'type': 'prompt', 'message': 'Wrong visible context', 'session_id': origin,
              'session_revision': ready['session_revision']})
        assert 'conversation changed' in read('error')['message'].lower() and not marker.exists()
        send({'type': 'session_control', 'action': 'fork', 'request_id': 'fork',
              'expected_session': target.session_id, 'session_revision': activated['session_revision'],
              'index': 0 if action == 'goto' else 1})
        saved = read('session_result')
        assert saved['success'] and saved['result'] in {
            branch['branch_id'] for branch in manager.load_session(target.session_id).branches}
        send({'type': 'status'})
        status = read('status')
        assert status['session_id'] == target.session_id and status['permission_mode'] == 'deny'
        send({'type': 'quit'})
        assert process.wait(timeout=6) == 0, process.stderr.read()
        thread.join(3)
        assert not thread.is_alive() and not marker.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
        thread.join(3)


async def test_reader_status_and_cancel_ack_work_while_admitted_disk_owner_settles(tmp_path, monkeypatch):
    owner, manager, _ = setup(tmp_path)
    original = owner.session_id
    target = manager.create_session(model='offline/model', working_dir=tmp_path)
    target.messages = [UserMessage(content='ADMITTED-TARGET')]
    manager.save_session(target)
    entered, release = threading.Event(), threading.Event()
    write = manager._write
    def held(data):
        write(data)
        if data.session_id == target.session_id:
            entered.set()
            assert release.wait(4)
    monkeypatch.setattr(manager, '_write', held)
    pending = queue.Queue()
    pending.put({'type': 'session_control', 'request_id': 'held', 'expected_session': original,
                 'action': 'resume', 'target_session': target.session_id})
    reads = [0]
    events = []
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
                    assert event['navigation_running'] and event['session_id'] == original
                    assert event['permission_mode'] == 'deny' and not release.is_set()
                    pending.put({'type': 'session_control_cancel', 'request_id': 'held', 'expected_session': original})
                elif event['type'] == 'session_cancel_requested':
                    assert not release.is_set()
                    release.set()
                elif event['type'] == 'session_result':
                    assert event['success'] and event['receipt_canceled'] and event['session_id'] == target.session_id
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
        assert owner.session_id == target.session_id and owner.permissions.mode == 'deny'
    finally:
        release.set()
        await close(owner)


@pytest.mark.parametrize('changed', [False, True])
async def test_failed_new_session_removes_only_its_unchanged_unadmitted_candidate(tmp_path, monkeypatch, changed):
    owner, manager, _ = setup(tmp_path)
    original = owner.session_id
    before = manager._session_path(original).read_bytes()
    candidates = []
    def failed(identifier):
        candidates.append(identifier)
        if changed:
            value = manager.load_session(identifier)
            value.name = 'Independently changed candidate'
            manager.save_session(value)
        raise ValueError('Target preflight rejected')
    monkeypatch.setattr(owner, 'switch_session', failed)
    try:
        with pytest.raises(ValueError, match='preflight'):
            owner.new_session()
        assert owner.session_id == original and manager._session_path(original).read_bytes() == before
        candidate = manager.load_session(candidates[0])
        assert (candidate is not None) is changed
        if changed:
            assert candidate.name == 'Independently changed candidate'
    finally:
        await close(owner)


def combined_target(manager, tmp_path, *, bad_effort=False):
    from xdog.agent.planning import PlanState
    from xdog.coding.core.messages import messages_to_dicts

    directory = tmp_path / 'target-workspace'
    directory.mkdir()
    target = manager.create_session(model='offline/model', working_dir=directory)
    target.messages = [UserMessage(content='TARGET-LIVE-HEAD'), UserMessage(content='TARGET-LIVE-TAIL')]
    target.settings = {'thinking_level': 'low'}
    target.branches = [{'branch_id': 'selected', 'branch_point': 1,
        'messages': messages_to_dicts([UserMessage(content='SELECTED-BRANCH'), UserMessage(content='BRANCH-TAIL')]),
        'model': 'offline/model', 'settings': {'thinking_level': 'high' if bad_effort else 'low'},
        'plan': PlanState(mode='planning', revision=7, objective='Restore selected plan').to_dict(),
        'extension_entries': {'navigator': [{'id': 'saved', 'kind': 'selection', 'data': 'BRANCH-STATE'}]},
        'compaction': {'source': 'builtin', 'first_kept_index': 1, 'original_count': 10,
                       'details': {'selection': 'branch'}}}]
    manager.save_session(target)
    return target


@pytest.mark.parametrize('selection', ['branch', 'head-checkpoint', 'branch-checkpoint'])
async def test_combined_rpc_selection_stages_only_selected_context_and_commits_target_once(
    tmp_path, monkeypatch, selection,
):
    owner, manager, adapter = setup(tmp_path)
    original = owner.session_id
    target = combined_target(manager, tmp_path)
    events, saves = [], []
    original_save = manager.save_session
    def observe(data):
        if data.session_id == target.session_id:
            saves.append((owner.session_id, list(data.messages), data.active_branch))
        original_save(data)
    monkeypatch.setattr(manager, 'save_session', observe)
    def no_intermediate(*args):
        raise AssertionError('Combined selection cannot resume then restore')
    monkeypatch.setattr(owner, 'switch_session', no_intermediate)
    monkeypatch.setattr(owner, 'restore_branch', no_intermediate)
    nav = RpcNavigation(owner, events.append)
    try:
        action = 'branch' if selection == 'branch' else 'goto'
        fields = {'target_session': target.session_id,
                  'branch_id': '' if selection == 'head-checkpoint' else 'selected'}
        if action == 'goto':
            fields['index'] = 0
        nav.start(packet(owner, nav, action, 'combined', **fields), turn_running=False, extension_running=False)
        await finish(nav)
        assert events[-1]['success'] and owner.session_id == target.session_id
        assert len(saves) == 1 and saves[0][0] == original
        assert owner.messages == saves[0][1]
        assert owner.session_data.active_branch == saves[0][2]
        expected = 'TARGET-LIVE-HEAD' if selection == 'head-checkpoint' else 'SELECTED-BRANCH'
        assert expected in str(owner.messages) and len(owner.messages) == (2 if selection == 'branch' else 1)
        assert owner.working_dir == tmp_path / 'target-workspace'
        if selection != 'head-checkpoint':
            assert owner.plan.state.mode == 'planning' and owner.plan.state.revision == 7
            assert owner.extensions.entries.for_extension('navigator')[0]['data'] == 'BRANCH-STATE'
            assert owner.session_data.compaction == target.branches[0]['compaction']
        persisted = manager.load_session(target.session_id)
        assert persisted.active_branch == owner.session_data.active_branch
        assert 'TARGET-LIVE-TAIL' in str(persisted.branches[-1]['messages'])
        assert 'ORIGINAL-HEAD' in str(manager.load_session(original).messages)
        assert owner.permissions.mode == 'deny' and not adapter.calls
        owner.navigate_checkpoint(target.session_id, branch_id=persisted.branches[-1]['branch_id'])
        assert 'TARGET-LIVE-TAIL' in str(owner.messages) and owner.plan.state.mode == 'off'
    finally:
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('failure', ['effort', 'save', 'checkpoint', 'branch', 'workspace', 'identity'])
async def test_combined_failure_never_adopts_target_live_head_or_persists_phantom_branch(
    tmp_path, monkeypatch, failure,
):
    from dataclasses import replace

    owner, manager, adapter = setup(tmp_path)
    target = combined_target(manager, tmp_path, bad_effort=failure == 'effort')
    adapter.profile = replace(adapter.profile, reasoning=True, supported_efforts=('low',))
    before_owner = owner.session_data
    before_messages = owner.agent.state.messages
    before_resources = owner.plan, owner.extensions, owner.children, owner.mcp
    if failure == 'identity':
        path = manager._session_path(target.session_id)
        mismatched = json.loads(path.read_text())
        mismatched['session_id'] = owner.session_id
        path.write_text(json.dumps(mismatched))
    before = manager._session_path(target.session_id).read_bytes()
    if failure == 'workspace':
        (tmp_path / 'target-workspace').rmdir()
    if failure == 'save':
        write = manager._write
        def fail(data):
            if data.session_id == target.session_id:
                raise OSError('Target save failure')
            write(data)
        monkeypatch.setattr(manager, '_write', fail)
    events = []
    nav = RpcNavigation(owner, events.append)
    try:
        nav.start(packet(owner, nav, 'goto', 'combined-fail', target_session=target.session_id,
            branch_id='missing' if failure == 'branch' else 'selected', index=99 if failure == 'checkpoint' else 0),
            turn_running=False, extension_running=False)
        await finish(nav)
        assert not events[-1]['success'] and not events[-1].get('state_changed')
        assert owner.session_data is before_owner and owner.agent.state.messages is before_messages
        assert (owner.plan, owner.extensions, owner.children, owner.mcp) == before_resources
        assert manager._session_path(target.session_id).read_bytes() == before
        assert owner.permissions.mode == 'deny' and not adapter.calls
    finally:
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('when', ['unadmitted', 'committed'])
async def test_combined_cancel_settles_one_staged_target_and_never_reports_invented_rollback(
    tmp_path, monkeypatch, when,
):
    owner, manager, adapter = setup(tmp_path)
    target = combined_target(manager, tmp_path)
    before = manager._session_path(target.session_id).read_bytes()
    original = owner.session_id
    entered, release = threading.Event(), threading.Event()
    if when == 'unadmitted':
        method = owner._stage_navigation
        def held(*args):
            entered.set()
            assert release.wait(4)
            return method(*args)
        monkeypatch.setattr(owner, '_stage_navigation', held)
    else:
        method = manager._write
        def held(data):
            method(data)
            if data.session_id == target.session_id:
                entered.set()
                assert release.wait(4)
        monkeypatch.setattr(manager, '_write', held)
    events = []
    nav = RpcNavigation(owner, events.append)
    try:
        nav.start(packet(owner, nav, 'goto', 'combined-cancel', target_session=target.session_id,
                         branch_id='selected', index=0), turn_running=False, extension_running=False)
        async with asyncio.timeout(4):
            while not entered.is_set():
                await asyncio.sleep(0.003)
        nav.cancel({'request_id': 'combined-cancel', 'expected_session': original})
        assert nav.busy
        release.set()
        await finish(nav)
        if when == 'unadmitted':
            assert events[-1]['canceled'] and owner.session_id == original
            assert manager._session_path(target.session_id).read_bytes() == before
        else:
            assert events[-1]['success'] and events[-1]['receipt_canceled']
            assert owner.session_id == target.session_id and len(owner.messages) == 1
            assert owner.messages[0].content[0].text == 'SELECTED-BRANCH'
            assert manager.load_session(target.session_id).active_branch == owner.session_data.active_branch
        assert not adapter.calls and owner.permissions.mode == 'deny'
    finally:
        release.set()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('action', ['branch', 'goto'])
@pytest.mark.parametrize('fail', [False, True])
async def test_native_slash_cross_session_selection_uses_same_single_staged_admission(tmp_path, action, fail):
    from dataclasses import replace

    from xdog.coding.core.slash_commands import execute_command

    owner, manager, adapter = setup(tmp_path)
    target = combined_target(manager, tmp_path, bad_effort=fail)
    adapter.profile = replace(adapter.profile, reasoning=True, supported_efforts=('low',))
    original, before = owner.session_data, manager._session_path(target.session_id).read_bytes()
    args = target.session_id + ' selected' + (' 1' if action == 'goto' else '')
    try:
        if fail:
            with pytest.raises(ValueError, match='thinking'):
                await execute_command(action, args, owner)
            assert owner.session_data is original
            assert manager._session_path(target.session_id).read_bytes() == before
        else:
            result = await execute_command(action, args, owner)
            assert ('Restored branch:' if action == 'branch' else 'Checkpoint branch:') in result.output
            assert owner.session_id == target.session_id and 'SELECTED-BRANCH' in str(owner.messages)
            assert owner.plan.state.mode == 'planning'
        assert owner.permissions.mode == 'deny' and not adapter.calls
    finally:
        await close(owner)


async def test_current_head_goto_preflight_failure_does_not_save_phantom_checkpoint(tmp_path, monkeypatch):
    owner, manager, adapter = setup(tmp_path)
    original = owner.session_data
    before = manager._session_path(owner.session_id).read_bytes()
    def fail(*args):
        raise ValueError('Selected checkpoint cannot activate')
    monkeypatch.setattr(owner, '_stage_navigation', fail)
    try:
        with pytest.raises(ValueError, match='cannot activate'):
            owner.goto_checkpoint(0)
        assert owner.session_data is original and not original.branches
        assert manager._session_path(owner.session_id).read_bytes() == before
        assert len(owner.messages) == 2 and not adapter.calls
    finally:
        await close(owner)


async def test_sdk_empty_checkpoint_sentinel_remains_valid_but_cannot_truncate_nonempty_context(tmp_path):
    owner, _, adapter = setup(tmp_path)
    try:
        with pytest.raises(ValueError, match='Checkpoint'):
            owner.goto_checkpoint(-1)
        owner.agent.clear_messages()
        owner._persist()
        branch = owner.goto_checkpoint(-1)
        assert owner.session_data.active_branch == branch and not owner.messages and not adapter.calls
    finally:
        await close(owner)


@pytest.mark.parametrize('corruption', ['records', 'messages', 'settings', 'model'])
async def test_corrupt_saved_branch_emits_owned_refusal_and_next_control_remains_usable(tmp_path, corruption):
    owner, manager, adapter = setup(tmp_path)
    target = combined_target(manager, tmp_path)
    path = manager._session_path(target.session_id)
    raw = json.loads(path.read_text())
    if corruption == 'records':
        raw['branches'] = [None]
    elif corruption == 'messages':
        raw['branches'][0].pop('messages')
    else:
        raw['branches'][0][corruption] = [] if corruption == 'settings' else 123
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    old_owner = owner.session_data
    events = []
    nav = RpcNavigation(owner, events.append)
    try:
        nav.start(packet(owner, nav, 'goto', 'bad-branch', target_session=target.session_id,
                         branch_id='selected', index=0), turn_running=False, extension_running=False)
        await finish(nav)
        assert events[-1]['request_id'] == 'bad-branch' and not events[-1]['success']
        assert owner.session_data is old_owner and path.read_bytes() == before
        nav.start(packet(owner, nav, 'list', 'next-read'), turn_running=False, extension_running=False)
        await finish(nav)
        assert events[-1]['success'] and events[-1]['request_id'] == 'next-read'
        assert owner.permissions.mode == 'deny' and not adapter.calls
    finally:
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('index', [1, 2, 3])
async def test_combined_checkpoint_refuses_partial_parallel_tool_batch_and_preserves_complete_batch(tmp_path, index):
    from xdog.ai.types import ToolCall, ToolResultMessage
    from xdog.coding.core.messages import messages_to_dicts

    owner, manager, adapter = setup(tmp_path)
    target = combined_target(manager, tmp_path)
    history = [UserMessage(content='TOOL-CHECKPOINT'), AssistantMessage(content=(
        ToolCall(id='first', name='filesystem', arguments={}),
        ToolCall(id='second', name='filesystem', arguments={}))),
        ToolResultMessage(tool_call_id='first', tool_name='filesystem', content=(TextContent(text='FIRST-RESULT'),)),
        ToolResultMessage(tool_call_id='second', tool_name='filesystem', content=(TextContent(text='SECOND-RESULT'),))]
    target.branches[0]['messages'] = messages_to_dicts(history)
    manager.save_session(target)
    before = manager._session_path(target.session_id).read_bytes()
    original = owner.session_data
    events = []
    nav = RpcNavigation(owner, events.append)
    try:
        nav.start(packet(owner, nav, 'goto', 'tool-checkpoint', target_session=target.session_id,
                         branch_id='selected', index=index), turn_running=False, extension_running=False)
        await finish(nav)
        assert events[-1]['success'] is (index == 3)
        if index == 3:
            assert len(owner.messages) == 4
            assert 'FIRST-RESULT' in str(owner.messages) and 'SECOND-RESULT' in str(owner.messages)
        else:
            assert owner.session_data is original
            assert manager._session_path(target.session_id).read_bytes() == before
        assert not adapter.calls and owner.permissions.mode == 'deny'
    finally:
        await nav.aclose()
        await close(owner)
