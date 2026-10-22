"""Goal consumes durable events from the real independent Eyes daemon."""
import asyncio
import json
import subprocess
import sys

import pytest
from xdog.skills.goal import eyes_cli
from xdog.skills.goal.automation import TaskObserver


def target(workspace):
    return {'endpoint': '/tmp/xdog-private/agent.sock', 'cwd': str(workspace),
            'session_id': 'original-session', 'inbox_id': 'original-inbox', 'branch_id': 'original-branch'}


async def ignored(delivery):
    return True


@pytest.fixture
async def daemon():
    pytest.importorskip('xdog.eyes')
    from test_support.eyes_daemon import isolated_eyes_daemon
    async with isolated_eyes_daemon(prefix='goal-cli-') as directory:
        yield directory


async def inspect_observer(observer, *arguments):
    return await eyes_cli.invoke(observer._owner, *arguments)


async def test_config_generated_without_eyes_and_no_package_install(tmp_path, monkeypatch):
    async def absent(*args, **kwargs):
        raise eyes_cli.EyesCLIError('Eyes CLI deliberately absent')
    monkeypatch.setattr(eyes_cli, 'invoke', absent)
    observer = TaskObserver()
    config = tmp_path / 'observer.json'
    result = await observer.start(target(tmp_path), tmp_path, config, 'g1', ignored)
    assert result['status'] == 'unavailable' and 'Eyes CLI deliberately absent' in result['reason']
    data = json.loads(config.read_text())
    assert data['sources'] == [{'id': 'tasks', 'type': 'file', 'root': str(tmp_path),
                                'include': ['*.json'], 'exclude': ['*/**', 'observer.json']}]
    assert data['handlers'] == [{'id': 'owner', 'type': 'events'}]  # No direct ACP bypass.
    await observer.aclose()


async def test_missing_endpoint_is_explicit_unavailable(tmp_path):
    observer = TaskObserver()
    result = await observer.start({}, tmp_path, tmp_path / 'config.json', 'g', ignored)
    assert result['status'] == 'unavailable' and 'endpoint' in result['reason']
    await observer.aclose()


async def test_requires_goal_consumer_instead_of_direct_model_input(tmp_path):
    observer = TaskObserver()
    result = await observer.start(target(tmp_path), tmp_path, tmp_path / 'config.json', 'g')
    assert result['status'] == 'unavailable' and 'consumer' in result['reason']
    await observer.aclose()


async def test_daemon_watches_only_task_json_and_goal_acknowledges_after_callback(tmp_path, daemon):
    directory = tmp_path / 'tasks'
    directory.mkdir()
    (directory / 'nested').mkdir()
    config = directory / 'observer.json'
    received, committed, release = [], asyncio.Event(), asyncio.Event()

    async def consume(delivery):
        received.append(delivery)
        committed.set()
        await release.wait()
        return True

    observer = TaskObserver()
    binding = target(tmp_path)
    try:
        result = await observer.start(binding, directory, config, 'g', consume)
        assert result['status'] == 'watching'
        binding['inbox_id'] = 'mutated-caller-data'
        assert (await inspect_observer(observer, 'status', result['registration_id']))['state'] == 'watching'
        (directory / 'a.json').write_text('{"status":"completed"}')
        (directory / 'ignore.md').write_text('not a task')
        (directory / 'nested' / 'ignore.json').write_text('{}')
        config.write_text(config.read_text() + '\n')
        await asyncio.wait_for(committed.wait(), 6)
        assert len(received) == 1
        assert received[0].events[0]['evidence'][0]['path'] == 'a.json'
        assert len((await inspect_observer(observer, 'events'))['events']) == 1  # Callback not committed yet.
        release.set()
        async with asyncio.timeout(3):
            while (await inspect_observer(observer, 'events'))['events']:
                await asyncio.sleep(.02)
    finally:
        release.set()
        await observer.aclose()
    assert observer._closed and observer._subscription.done()
    count = len(received)
    (directory / 'a.json').write_text('{"status":"pending"}')
    await asyncio.sleep(.1)
    assert len(received) == count


async def test_host_detach_preserves_registration_and_offline_events_for_same_owner(tmp_path, daemon):
    observer = TaskObserver()
    first = await observer.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', ignored)
    await observer.aclose(suspend=False)
    (tmp_path / 'task.json').write_text('{}')
    received = asyncio.Event()
    deliveries = []

    async def consume(delivery):
        deliveries.append(delivery)
        received.set()
        return True

    restored = TaskObserver()
    try:
        result = await restored.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', consume)
        assert result['registration_id'] == first['registration_id']
        await asyncio.wait_for(received.wait(), 6)
        assert deliveries[0].events[0]['evidence'][0]['path'] == 'task.json'
    finally:
        await restored.aclose()


async def test_pause_then_restore_reuses_original_registration(tmp_path, daemon):
    first = TaskObserver()
    initial = await first.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', ignored)
    await first.aclose()  # Business pause, unlike host detach.
    restored = TaskObserver()
    try:
        current = await restored.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', ignored)
        assert current['status'] == 'watching'
        assert current['registration_id'] == initial['registration_id']
    finally:
        await restored.aclose()


async def test_callback_failure_retains_unacknowledged_event(tmp_path, daemon):
    failed = asyncio.Event()

    async def failing(delivery):
        failed.set()
        raise OSError('Goal durable commit failed')

    observer = TaskObserver()
    status = await observer.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', failing)
    try:
        (tmp_path / 'task.json').write_text('{}')
        await asyncio.wait_for(failed.wait(), 6)
        await observer._subscription
        assert status['status'] == 'unavailable'
        assert len((await inspect_observer(observer, 'events'))['events']) == 1
    finally:
        await observer.aclose(suspend=False)
    received = asyncio.Event()

    async def consume(delivery):
        received.set()
        return True

    restored = TaskObserver()
    try:
        await restored.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', consume)
        await asyncio.wait_for(received.wait(), 3)
    finally:
        await restored.aclose()


async def test_close_joins_pending_start_and_baseline_readiness(tmp_path, monkeypatch, daemon):
    entered, released = asyncio.Event(), asyncio.Event()
    original = eyes_cli.invoke

    async def pending(owner, *arguments, **kwargs):
        if arguments[0] == 'status' and '--wait-ready' in arguments:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        return await original(owner, *arguments, **kwargs)

    monkeypatch.setattr(eyes_cli, 'invoke', pending)
    observer = TaskObserver()
    start = asyncio.create_task(observer.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', ignored))
    await asyncio.wait_for(entered.wait(), 5)
    await asyncio.wait_for(observer.aclose(), 3)
    assert released.is_set() and observer._start_task.done() and observer._closed
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
    result = await observer.start(target(workspace), tasks, config, 'g', ignored)
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


async def test_restored_goal_receives_offline_daemon_event_before_reminding_main_agent(tmp_path, monkeypatch, daemon):
    import xdog.skills.goal.automation as module
    from xdog.skills.goal.host import OperationContext
    from xdog.skills.goal.runtime import GoalRuntime

    from packages.skills.tests.goal.test_runtime import close, setup

    runtime, parent, host, state, sent, _, listeners = await setup(tmp_path, monkeypatch)
    monkeypatch.setattr(module, 'TaskObserver', TaskObserver)
    state['state'] = 'running'  # Events may persist but must not start a busy owner.
    recovered = None
    try:
        goal = await runtime.service.create('Offline task', 'Observe, coalesce, then remind',
                                            context=OperationContext(True))
        await runtime.service.task({'id': 'work', 'title': 'Work', 'expected_version': 0})
        await runtime.service.change('wait', until='tasks', reason='Worker owns the task')
        await runtime.flush()
        registration = runtime.automation['registration_id']
        files = runtime.service.files(goal)
        await runtime.aclose()  # Host retirement detaches; Eyes keeps observing.
        await asyncio.to_thread(files.update, 'work', 1, {'status': 'completed', 'summary': 'Worker finished'})
        recovered = await GoalRuntime.open(host)
        recovered.attach(parent)
        await recovered.flush()
        assert recovered.automation['registration_id'] == registration
        assert (await recovered.service.current()).wait_for == 'tasks'
        async with asyncio.timeout(6):
            while True:
                signals = await recovered.service.store.wake_state(goal.id)
                if any(cause.startswith('tasks:') for cause in signals['causes']):
                    break
                await asyncio.sleep(.05)
        assert sent == []  # Eyes reached Goal runtime, not direct ACP/model input.
        state['state'] = 'idle'
        listeners[0]({'type': 'owner.available'})
        await recovered.flush()
        assert len(sent) == 1
        assert sent[0].source == 'goal'
        assert any(cause.startswith('tasks:') for cause in sent[0].provenance['triggers'])
    finally:
        await close(recovered or runtime, parent)


async def test_live_goal_subscription_reconnects_after_daemon_restart(tmp_path, daemon):
    received = asyncio.Event()

    async def consume(delivery):
        received.set()
        return True

    observer = TaskObserver()
    try:
        result = await observer.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', consume)
        await inspect_observer(observer, 'daemon', 'stop')
        (tmp_path / 'after-restart.json').write_text('{}')
        # Follow is read-only and must not undo an explicit daemon stop.
        await inspect_observer(observer, 'daemon', 'start')
        await asyncio.wait_for(received.wait(), 10)
        assert (await inspect_observer(observer, 'status', result['registration_id']))['registration_id'] == (
            result['registration_id'])
        assert observer._state['status'] == 'watching'
    finally:
        await observer.aclose(suspend=False)


async def test_new_transport_address_reuses_owner_but_new_inbox_cannot_take_over(tmp_path, daemon):
    first = TaskObserver()
    initial = await first.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', ignored)
    await first.aclose(suspend=False)
    binding = {**target(tmp_path), 'endpoint': '/tmp/new-private/agent.sock'}
    restored = TaskObserver()
    unrelated = TaskObserver()
    try:
        current = await restored.start(binding, tmp_path, tmp_path / 'eyes.json', 'g', ignored)
        assert current['registration_id'] == initial['registration_id']
        other = await unrelated.start({**binding, 'inbox_id': 'different-inbox'},
                                       tmp_path, tmp_path / 'eyes.json', 'g', ignored)
        assert other['registration_id'] != initial['registration_id']
    finally:
        await restored.aclose()
        await unrelated.aclose()


async def test_non_accepting_owner_callback_does_not_ack_or_drop_daemon_event(tmp_path, daemon):
    declined = asyncio.Event()

    async def no_longer_owned(delivery):
        declined.set()
        return False  # A normal callback return is not proof of durable commit.

    observer = TaskObserver()
    status = await observer.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', no_longer_owned)
    try:
        (tmp_path / 'task.json').write_text('{}')
        await asyncio.wait_for(declined.wait(), 6)
        await observer._subscription
        assert status['status'] == 'unavailable'
        assert len((await inspect_observer(observer, 'events'))['events']) == 1
    finally:
        await observer.aclose(suspend=False)
    received = asyncio.Event()

    async def accepted(delivery):
        received.set()
        return True

    restored = TaskObserver()
    try:
        await restored.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', accepted)
        await asyncio.wait_for(received.wait(), 3)
    finally:
        await restored.aclose()


async def test_cancel_after_daemon_registration_commit_joins_ack_and_suspends_original(tmp_path, daemon, monkeypatch):
    accepted, release = asyncio.Event(), asyncio.Event()
    original = eyes_cli.invoke
    committed = {}

    async def delayed(owner, *arguments, **kwargs):
        result = await original(owner, *arguments, **kwargs)
        if arguments[0] == 'start':
            committed.update(result)
            accepted.set()
            await release.wait()  # CLI accepted; TaskObserver has not seen its ID.
        return result

    monkeypatch.setattr(eyes_cli, 'invoke', delayed)
    observer = TaskObserver()
    starting = asyncio.create_task(observer.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', ignored))
    await asyncio.wait_for(accepted.wait(), 5)
    closing = asyncio.create_task(observer.aclose())
    await asyncio.sleep(.03)
    assert not closing.done() and observer._registration_id is None
    release.set()
    await asyncio.wait_for(closing, 3)
    with pytest.raises(asyncio.CancelledError):
        await starting
    assert (await inspect_observer(observer, 'status', committed['registration_id']))['state'] == 'paused'
    assert (await inspect_observer(observer, 'daemon', 'status'))['state'] == 'running'


async def test_registration_ack_loss_cleanup_uses_original_scoped_key(tmp_path, daemon, monkeypatch):
    original = eyes_cli.invoke
    committed = {}

    async def lost(owner, *arguments, **kwargs):
        result = await original(owner, *arguments, **kwargs)
        if arguments[0] == 'start':
            committed.update(result)
            raise eyes_cli.EyesCLIError('Registration response lost after server commit')
        return result

    monkeypatch.setattr(eyes_cli, 'invoke', lost)
    observer = TaskObserver()
    result = await observer.start(target(tmp_path), tmp_path, tmp_path / 'eyes.json', 'g', ignored)
    assert result['status'] == 'unavailable' and observer._registration_id is None
    await observer.aclose()
    assert (await inspect_observer(observer, 'status', committed['registration_id']))['state'] == 'paused'
