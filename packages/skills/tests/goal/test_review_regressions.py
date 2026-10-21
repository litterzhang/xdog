"""Sidecar safety regressions for the simplified Goal core (no host or model loop)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from xdog.agent.capabilities.runtime import ParentPort
from xdog.skills.goal.host import OperationContext
from xdog.skills.goal.models import Owner
from xdog.skills.goal.runtime import GoalRuntime
from xdog.skills.goal.service import GoalService
from xdog.skills.goal.store import GoalStore

USER = OperationContext(True)


async def service_at(tmp_path):
    return GoalService(await GoalStore.open(tmp_path / 'data/registry.sqlite3', Owner('review', tmp_path)))


class Host:
    scope = 'group'
    owner_prefix = 'review'
    namespace = 'inbox'
    identity = 'group'
    branch = ''

    def __init__(self, root):
        self.workspace = root
        self.storage_root = root / 'host'
        self.target = {'endpoint': '/tmp/review-private/agent.sock', 'session_id': 'group:group',
                       'cwd': str(root), 'inbox_id': 'inbox', 'branch_id': ''}
        self.parent = None

    def valid(self):
        return True

    def accepting(self):
        return True

    def subscribe_events(self, callback):
        return lambda: None

    def owns_parent(self, parent):
        return parent is self.parent

    def execution_snapshot(self):
        return {'state': 'idle', 'user_pending': False, 'children_active': False, 'generation': 'g'}

    async def acp_target(self):
        return self.target.copy() if self.target else None


class NativeExtensions:
    enabled = ()

    def bind_native_commands(self, *args, **kwargs):
        return lambda: None


def parent_for(host):
    parent = ParentPort(agent=object(), extensions=NativeExtensions(), children=None,
                        valid=lambda: True, user_authorized=lambda: False)
    host.parent = parent
    return parent


@pytest.mark.parametrize('until', ['user', 'host'])
async def test_automatic_input_cannot_launder_protected_wait_through_task_wait(tmp_path, until):
    service = await service_at(tmp_path)
    try:
        await service.create('One', 'Original user outcome', context=USER)
        await service.change('wait', until=until)
        with pytest.raises(ValueError, match='user|wait|resume'):
            await service.change('wait', until='tasks')
        goal = await service.current()
        assert goal.status == 'waiting' and goal.wait_for == until
    finally:
        await service.close()


async def test_paused_outbox_row_cannot_be_promoted_and_sent(tmp_path, monkeypatch):
    import xdog.agent.acp.client as client
    host = Host(tmp_path)
    runtime = await GoalRuntime.open(host)
    entered, proceed = asyncio.Event(), asyncio.Event()
    sends = []
    original = runtime.service.store.claim_reminder

    async def claim(identifier, goal):
        entered.set()
        await proceed.wait()
        return await original(identifier, goal)

    async def enqueue(target, message):
        sends.append(message)
        return {'status': 'queued'}

    monkeypatch.setattr(runtime.service.store, 'claim_reminder', claim)
    monkeypatch.setattr(client, 'enqueue', enqueue)
    # Keep the test scoped to wake/intake, not observer implementations.
    runtime.service.on_change = lambda: None
    wake = None
    try:
        await runtime.service.create('One', 'Do useful work', context=USER)
        wake = asyncio.create_task(runtime._wake('execution:original'))
        await asyncio.wait_for(entered.wait(), 2)
        await runtime.service.change('pause', context=USER)
        assert (await runtime.service.store.reminders())[0]['status'] == 'cancelled'
        proceed.set()
        await wake
        assert sends == [], 'Pause must fence new sends, not only cancel a pending row'
        assert (await runtime.service.store.reminders())[0]['status'] == 'cancelled'
    finally:
        proceed.set()
        if wake is not None:
            await asyncio.gather(wake, return_exceptions=True)
        await runtime.aclose()


async def test_endpoint_unavailable_closes_existing_owned_observation(tmp_path):
    host = Host(tmp_path)
    runtime = await GoalRuntime.open(host)
    closed = asyncio.Event()

    async def aclose():
        closed.set()

    runtime.service.on_change = lambda: None
    try:
        await runtime.service.create('One', 'Keep original destination', context=USER)
        runtime._observer = SimpleNamespace(aclose=aclose)
        runtime._observer_key = ('old', 'target')
        host.target = None
        await runtime._refresh()
        assert closed.is_set(), 'Unavailable original endpoint must stop the existing owned registration'
    finally:
        await runtime.aclose()


async def test_unavailable_start_can_be_retried_when_same_owner_becomes_available(tmp_path, monkeypatch):
    from xdog.skills.goal import automation
    host = Host(tmp_path)
    runtime = await GoalRuntime.open(host)
    calls = []

    class Observer:
        async def start(self, *args):
            calls.append(args)
            return {'status': 'unavailable' if len(calls) == 1 else 'watching'}

        async def aclose(self):
            pass

    monkeypatch.setattr(automation, 'TaskObserver', Observer)
    runtime.service.on_change = lambda: None
    try:
        await runtime.service.create('One', 'Recover actual endpoint', context=USER)
        await runtime._refresh()
        assert runtime.automation['status'] == 'unavailable'
        runtime._event({'type': 'owner.available'})
        await runtime.flush()
        assert len(calls) == 2 and runtime.automation['status'] == 'watching'
    finally:
        await runtime.aclose()


async def test_stale_parent_tool_does_not_write_after_await(tmp_path, monkeypatch):
    from xdog.skills.goal import service as service_module
    host = Host(tmp_path)
    runtime = await GoalRuntime.open(host)
    runtime.service.on_change = lambda: None
    runtime._changed = lambda: None
    # Avoid observer/network behavior entirely; this is a pinned tool capability test.
    async def refresh():
        pass
    monkeypatch.setattr(runtime, '_refresh', refresh)
    entered, proceed = asyncio.Event(), asyncio.Event()
    original = service_module.run_owned

    async def work(operation, *args, **kwargs):
        if getattr(operation, '__name__', '') == 'write_plan':
            entered.set()
            await proceed.wait()
        return await original(operation, *args, **kwargs)

    pending = None
    try:
        goal = await runtime.service.create('One', 'Parent fenced state', context=USER)
        runtime.attach(parent_for(host))
        old = runtime.tools[0]
        monkeypatch.setattr(service_module, 'run_owned', work)
        pending = asyncio.create_task(old.execute('old', {'action': 'plan', 'content': 'stale parent write'}))
        await asyncio.wait_for(entered.wait(), 2)
        runtime.attach(parent_for(host))
        proceed.set()
        result = await pending
        assert result.is_error
        assert runtime.service.files(goal).plan() == '', 'Error after a stale write is not an ownership fence'
    finally:
        proceed.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await runtime.aclose()


async def test_service_close_joins_inflight_readonly_file_operation(tmp_path, monkeypatch):
    from xdog.skills.goal import service as service_module
    service = await service_at(tmp_path)
    original = service_module.run_owned
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def work(operation, *args, **kwargs):
        if getattr(operation, '__name__', '') == 'list':
            entered.set()
            await proceed.wait()
        return await original(operation, *args, **kwargs)

    read = close = None
    try:
        await service.create('One', 'Read ownership', context=USER)
        monkeypatch.setattr(service_module, 'run_owned', work)
        read = asyncio.create_task(service.status())
        await asyncio.wait_for(entered.wait(), 2)
        close = asyncio.create_task(service.close())
        done, _ = await asyncio.wait({close}, timeout=.05)
        assert not done, 'close returned before its admitted readonly operation settled'
    finally:
        proceed.set()
        await asyncio.gather(*(task for task in (read, close) if task is not None), return_exceptions=True)
        await service.close()


async def test_close_surfaces_failure_from_an_admitted_background_obligation(tmp_path):
    runtime = await GoalRuntime.open(Host(tmp_path))
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def obligation():
        entered.set()
        await proceed.wait()
        raise OSError('Admitted Goal obligation failed')

    runtime._schedule(obligation)
    await asyncio.wait_for(entered.wait(), 2)
    close = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    proceed.set()
    with pytest.raises(OSError, match='obligation failed'):
        await close


async def test_claim_reminder_is_atomic_and_rejects_stale_current_revision(tmp_path):
    service = await service_at(tmp_path)
    try:
        goal = await service.create('One', 'Atomically admitted original send', context=USER)
        await service.store.reminder(goal, {'id': 'one', 'instruction': 'original'})
        results = await asyncio.gather(*(service.store.claim_reminder('one', goal) for _ in range(2)))
        assert sorted(results) == [False, True]
        assert (await service.store.reminders())[0]['status'] == 'sending'
        await service.store.reminder(goal, {'id': 'stale', 'instruction': 'original revision',
                                            'provenance': {'goal_id': goal.id, 'goal_version': goal.version}})
        await service.plan('New main-Agent plan and Goal revision')
        assert await service.store.claim_reminder('stale', goal) is False
        assert await service.store.claim_reminder('stale', await service.current()) is False
        assert await service.store.claim_reminder('missing', await service.current()) is False
        await service.change('pause', context=USER)
        assert await service.store.claim_reminder('stale', goal) is False
        assert (await service.store.reminders())[1]['status'] == 'cancelled'
    finally:
        await service.close()


@pytest.mark.parametrize('until', ['user', 'host'])
async def test_automatic_completion_cannot_release_protected_wait(tmp_path, until):
    service = await service_at(tmp_path)
    try:
        await service.create('One', 'Inspect before any completion', context=USER)
        await service.change('wait', until=until)
        with pytest.raises(ValueError, match='user operation'):
            await service.change('complete', summary='Automatic attempt to skip protected wait')
        assert (await service.current()).wait_for == until
        waiting = await service.current()
        await service.change('activate', waiting.id, version=waiting.version,
                             reason='Actual user decision received', context=USER)
        assert (await service.change('complete', summary='Main Agent actually finished')).status == 'completed'
    finally:
        await service.close()


async def test_invocation_nested_pins_restore_without_lending_parent_identity_to_new_tasks(tmp_path):
    service = await service_at(tmp_path)
    valid = True
    try:
        goal = await service.create('One', 'Invocation pins', context=USER)
        with service.invocation(lambda: valid):
            assert await service.current() == goal
            with pytest.raises(ValueError, match='owner retired'):
                with service.invocation(lambda: False):
                    pass
            assert await service.current() == goal
            valid = False
            with pytest.raises(ValueError, match='owner retired'):
                await service.current()
            # A separately admitted host event has its own actual task identity;
            # a copied ContextVar alone must not bind it to the disposed parent.
            assert await asyncio.create_task(service.current()) == goal
        assert await service.current() == goal
    finally:
        await service.close()


async def test_retired_invocation_cannot_commit_after_waiting_for_database_admission(tmp_path, monkeypatch):
    service = await service_at(tmp_path)
    entered, proceed = asyncio.Event(), asyncio.Event()
    valid = True
    original = service.store.put

    async def put(*args, **kwargs):
        entered.set()
        await proceed.wait()
        return await original(*args, **kwargs)

    async def change():
        with service.invocation(lambda: valid):
            await service.change('wait', until='tasks')

    pending = None
    try:
        goal = await service.create('One', 'No late DB mutation', context=USER)
        monkeypatch.setattr(service.store, 'put', put)
        pending = asyncio.create_task(change())
        await asyncio.wait_for(entered.wait(), 2)
        valid = False
        proceed.set()
        with pytest.raises(ValueError, match='owner retired'):
            await pending
        assert await service.current() == goal
    finally:
        proceed.set()
        if pending is not None:
            await asyncio.gather(pending, return_exceptions=True)
        await service.close()


@pytest.mark.parametrize('outcome', ['sending', 'cancelled', 'accepted', 'unknown'])
async def test_unclaimed_cancelled_reminder_cannot_be_settled_or_revived(tmp_path, outcome):
    service = await service_at(tmp_path)
    try:
        goal = await service.create('One', 'Cancelled is not sending', context=USER)
        await service.store.reminder(goal, {'id': 'cancelled', 'instruction': 'Never admitted'})
        await service.change('pause', context=USER)
        before = await service.store.reminders()
        with pytest.raises(ValueError, match='sending|claim_reminder'):
            await service.store.settle_reminder('cancelled', outcome)
        assert await service.store.reminders() == before
    finally:
        await service.close()


@pytest.mark.parametrize('outcome', ['accepted', 'unknown'])
async def test_settlement_requires_claim_and_is_terminal_but_survives_goal_pause(tmp_path, outcome):
    service = await service_at(tmp_path)
    try:
        goal = await service.create('One', 'Claimed work has independent settlement', context=USER)
        await service.store.reminder(goal, {'id': 'original', 'instruction': 'Original send'})
        for identifier in ('original', 'missing'):
            with pytest.raises(ValueError, match='sending'):
                await service.store.settle_reminder(identifier, outcome)
        assert await service.store.claim_reminder('original', goal)
        await service.change('pause', context=USER)
        await service.store.settle_reminder('original', outcome, 'Original receipt fact')
        settled = await service.store.reminders()
        assert settled[0]['status'] == outcome
        for replacement in ('accepted', 'unknown'):
            with pytest.raises(ValueError, match='sending'):
                await service.store.settle_reminder('original', replacement, 'Cannot rewrite final receipt')
        assert await service.store.reminders() == settled
    finally:
        await service.close()
