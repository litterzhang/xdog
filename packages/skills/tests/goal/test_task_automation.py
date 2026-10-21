"""Owned optional Eyes embedding, using real file collection and public service."""
import asyncio
import builtins
import json
import subprocess
import sys

import pytest
from xdog.skills.goal import automation
from xdog.skills.goal.automation import TaskObserver


def target(workspace):
    return {'endpoint': '/tmp/xdog-private/agent.sock', 'cwd': str(workspace),
            'session_id': 'original-session', 'inbox_id': 'original-inbox', 'branch_id': 'original-branch'}


async def test_config_generated_without_eyes_and_no_package_install(tmp_path, monkeypatch):
    directory = tmp_path / 'tasks'
    directory.mkdir()
    config = directory / 'observer.json'
    original = builtins.__import__

    def absent(name, *args, **kwargs):
        if name == 'xdog.eyes.service':
            raise ImportError('Eyes deliberately absent')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', absent)
    observer = TaskObserver()
    result = await observer.start(target(tmp_path), directory, config, 'g1')
    assert result['status'] == 'unavailable'
    assert 'Eyes deliberately absent' in result['reason']
    data = json.loads(config.read_text())
    assert data['version'] == 2
    assert data['sources'] == [{'id': 'tasks', 'type': 'file', 'root': str(directory),
                                'include': ['*.json'], 'exclude': ['*/**', 'observer.json']}]
    assert data['handlers'][0]['delivery'] == 'enqueue'
    assert data['handlers'][0]['session'] == 'original-session'
    await observer.aclose()


async def test_missing_endpoint_is_explicit_unavailable(tmp_path):
    observer = TaskObserver()
    result = await observer.start({}, tmp_path, tmp_path / 'config.json', 'g')
    assert result['status'] == 'unavailable' and 'endpoint' in result['reason']
    await observer.aclose()


async def test_real_eyes_watches_only_task_json_and_delivers_to_pinned_scope(tmp_path, monkeypatch):
    pytest.importorskip('xdog.eyes.service')
    directory = tmp_path / 'tasks'
    directory.mkdir()
    (directory / 'nested').mkdir()
    config = directory / 'observer.json'
    calls, messages = [], []
    sent = asyncio.Event()

    async def status(binding):
        calls.append(binding.copy())
        return {'messages': []}

    async def enqueue(binding, message):
        calls.append(binding.copy())
        messages.append(message)
        sent.set()
        return {'status': 'queued', 'message_state': 'pending'}

    monkeypatch.setattr(automation, 'inbox_status', status)
    monkeypatch.setattr(automation, 'enqueue', enqueue)
    observer = TaskObserver()
    original = target(tmp_path)
    try:
        started = await observer.start(original, directory, config, 'g')
        assert started['status'] == 'watching'
        original['inbox_id'] = 'mutated-caller-data'
        # start itself is a baseline-ready barrier: the caller/worker must not
        # need a private polling workaround before its first task update.
        state = await observer._service.status(started['registration_id'])
        assert state['registrations'][0]['state'] == 'watching'
        (directory / 'a.json').write_text('{"status":"completed"}')
        (directory / 'ignore.md').write_text('not a task')
        (directory / 'nested' / 'ignore.json').write_text('{}')
        config.write_text(config.read_text() + '\n')
        await asyncio.wait_for(sent.wait(), 5)
        assert len(messages) == 1
        assert messages[0].origin == 'automatic'
        assert messages[0].evidence[0]['evidence'][0]['path'] == 'a.json'
        assert all(call == target(tmp_path) for call in calls)
        assert 'not user authorization' in messages[0].instruction
    finally:
        await observer.aclose()
    assert not observer._service.entries
    count = len(messages)
    (directory / 'a.json').write_text('{"status":"pending"}')
    await asyncio.sleep(.05)
    assert len(messages) == count  # Close owns only observation, no accepted-input withdrawal.


async def test_start_failure_closes_owned_service(tmp_path, monkeypatch):
    pytest.importorskip('xdog.eyes.service')

    async def unavailable(_):
        raise ConnectionError('Original ACP endpoint unavailable')

    monkeypatch.setattr(automation, 'inbox_status', unavailable)
    observer = TaskObserver()
    result = await observer.start(target(tmp_path), tmp_path, tmp_path / 'observer.json', 'g')
    assert result['status'] == 'unavailable'
    assert 'Original ACP endpoint unavailable' in result['reason']
    assert not observer._service.entries
    await observer.aclose()


async def test_close_joins_pending_owned_start_and_is_idempotent(tmp_path, monkeypatch):
    pytest.importorskip('xdog.eyes.service')
    entered, released = asyncio.Event(), asyncio.Event()

    async def pending(_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    monkeypatch.setattr(automation, 'inbox_status', pending)
    observer = TaskObserver()
    start = asyncio.create_task(observer.start(target(tmp_path), tmp_path, tmp_path / 'config.json', 'g'))
    await asyncio.wait_for(entered.wait(), 2)
    await asyncio.wait_for(observer.aclose(), 2)
    assert released.is_set()
    assert observer._start_task.done()
    assert not observer._service.entries
    with pytest.raises(asyncio.CancelledError):
        await start
    await observer.aclose()


@pytest.mark.parametrize('outside', ['tasks', 'config'])
async def test_paths_cannot_escape_original_workspace(tmp_path, outside):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    tasks = tmp_path if outside == 'tasks' else workspace
    config = tmp_path / 'observer.json' if outside == 'config' else workspace / 'observer.json'
    observer = TaskObserver()
    result = await observer.start(target(workspace), tasks, config, 'g')
    assert result['status'] == 'unavailable' and 'workspace' in result['reason']
    await observer.aclose()


def test_automation_import_requires_neither_eyes_nor_sdk():
    code = '''
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'acp' or name.startswith('acp.') or name == 'xdog.eyes' or name.startswith('xdog.eyes.'):
        raise ImportError('Optional package deliberately absent')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import xdog.skills.goal.automation
'''
    completed = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


async def test_start_does_not_report_watching_before_public_baseline_readiness(tmp_path, monkeypatch):
    service = pytest.importorskip('xdog.eyes.service')
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.ObserverService

    class DelayedReadiness(original):
        async def status(self, identifier=None):
            value = await super().status(identifier)
            if not release.is_set():
                entered.set()
                for row in value['registrations']:
                    row['state'] = 'baselining'
            return value

    async def available(_):
        return {'messages': []}

    monkeypatch.setattr(service, 'ObserverService', DelayedReadiness)
    monkeypatch.setattr(automation, 'inbox_status', available)
    observer = TaskObserver()
    start = asyncio.create_task(observer.start(target(tmp_path), tmp_path, tmp_path / 'config.json', 'goal'))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(.03)
        assert not start.done()
        release.set()
        result = await asyncio.wait_for(start, 2)
        assert result['status'] == 'watching'
    finally:
        release.set()
        await observer.aclose()
        await asyncio.gather(start, return_exceptions=True)
