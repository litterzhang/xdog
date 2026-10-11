"""Bounded original-loop task lifecycle; not task transport/host consent evidence."""
from __future__ import annotations

import asyncio

import pytest
from xdog.agent.mcp.task_registry import MAX_TASKS, TaskRegistry, TaskRegistryError


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


async def test_task_state_review_transition_result_copy_and_secret_free_catalog():
    registry = TaskRegistry()
    entered, release = asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        registry.input_required(identifier, True)
        entered.set()
        await release.wait()
        registry.input_required(identifier, False)
        return {'content': {'text': 'PRIVATE REVIEWED RESULT'}}
    try:
        created = registry.create('sampling/createMessage', operation)
        identifier = created['task']['taskId']
        await entered.wait()
        assert registry.state(identifier)['status'] == 'input_required'
        assert 'PRIVATE' not in str(registry.list())
        with pytest.raises(TaskRegistryError, match='not completed'):
            registry.result(identifier)
        release.set()
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        value = registry.result(identifier)
        value['content']['text'] = 'mutated copy'
        assert registry.result(identifier)['content']['text'] == 'PRIVATE REVIEWED RESULT'
        assert (await registry.cancel(identifier))['status'] == 'completed'
        with pytest.raises(TaskRegistryError, match='transition'):
            registry.input_required(identifier, True)
    finally:
        release.set()
        await registry.close()
    assert registry._retained_bytes == 0 and not registry._tasks


async def test_cancellation_waits_actual_finally_and_blocks_suppressed_late_result():
    registry = TaskRegistry()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cleanup.set()
            await release.wait()
            return {'private': 'MUST NOT RELEASE'}
    identifier = registry.create('elicitation/create', operation)['task']['taskId']
    await entered.wait()
    work = asyncio.create_task(registry.cancel(identifier))
    try:
        await cleanup.wait()
        assert not work.done() and registry.state(identifier)['status'] == 'working'
        work.cancel()
        await asyncio.sleep(.02)
        assert not work.done()  # Control cancellation does not abandon callback cleanup.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert registry.state(identifier)['status'] == 'cancelled'
        with pytest.raises(TaskRegistryError, match='not completed'):
            registry.result(identifier)
        assert registry._retained_bytes == 0
    finally:
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        await registry.close()


async def test_cancel_before_task_first_step_is_terminal_and_does_not_execute():
    registry, calls = TaskRegistry(), []
    async def operation(identifier):
        calls.append(identifier)
        return {}
    try:
        identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
        assert (await registry.cancel(identifier))['status'] == 'cancelled'
        assert not calls
    finally:
        await registry.close()


async def test_expired_tasks_refuse_reads_and_sweep_waits_owned_cleanup_then_frees_capacity():
    clock = [1.0]
    registry = TaskRegistry(clock=lambda: clock[0])
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
    identifier = registry.create('sampling/createMessage', operation, ttl=100)['task']['taskId']
    await entered.wait()
    clock[0] = 2
    assert registry.list() == {'tasks': []}
    with pytest.raises(TaskRegistryError, match='expired'):
        registry.state(identifier)
    sweep = asyncio.create_task(registry.sweep())
    try:
        await cleanup.wait()
        assert not sweep.done() and identifier in registry._tasks
        release.set()
        await sweep
        assert not registry._tasks and registry._retained_bytes == 0
    finally:
        release.set()
        await asyncio.gather(sweep, return_exceptions=True)
        await registry.close()


@pytest.mark.parametrize('failure', ['exception', 'non-json', 'oversize'])
async def test_failed_operations_or_oversized_results_never_leak_diagnostics_or_partial_data(failure):
    registry = TaskRegistry()
    async def operation(identifier):
        if failure == 'exception':
            raise RuntimeError('PRIVATE TOKEN')
        return {'body': object()} if failure == 'non-json' else {'body': 'x' * (256 * 1024 + 1)}
    try:
        identifier = registry.create('elicitation/create', operation)['task']['taskId']
        await until(lambda: registry.state(identifier)['status'] == 'failed')
        assert 'PRIVATE' not in str(registry.list())
        with pytest.raises(TaskRegistryError):
            registry.result(identifier)
        assert registry._retained_bytes == 0
    finally:
        await registry.close()


@pytest.mark.parametrize('ttl', [True, None, 0, -1, float('nan'), float('inf'), 600001])
async def test_invalid_retention_refuses_before_operation_admission(ttl):
    registry = TaskRegistry()
    async def operation(identifier):
        pytest.fail('No invalid task execution')
    with pytest.raises(TaskRegistryError):
        registry.create('sampling/createMessage', operation, ttl=ttl)
    assert not registry._tasks
    await registry.close()


async def test_catalog_capacity_and_aggregate_result_budget_are_bounded():
    registry = TaskRegistry()
    async def operation(identifier):
        return {'body': 'x' * 200000}
    try:
        identifiers = [registry.create('elicitation/create', operation)['task']['taskId'] for _ in range(MAX_TASKS)]
        with pytest.raises(TaskRegistryError, match='capacity'):
            registry.create('sampling/createMessage', operation)
        await until(lambda: all(registry.state(key)['status'] in ('completed', 'failed') for key in identifiers))
        assert registry._retained_bytes <= 1024 * 1024
        assert sum(registry.state(key)['status'] == 'completed' for key in identifiers) == 5
        assert sum(registry.state(key)['status'] == 'failed' for key in identifiers) == 11
        assert len(registry.list()['tasks']) == MAX_TASKS
    finally:
        await registry.close()
    assert registry._retained_bytes == 0


async def test_task_identity_scope_cannot_cross_registry_or_closed_connection():
    one, two = TaskRegistry(), TaskRegistry()
    async def operation(identifier):
        return {}
    try:
        identifier = one.create('sampling/createMessage', operation)['task']['taskId']
        with pytest.raises(TaskRegistryError, match='unavailable'):
            two.state(identifier)
        await one.close()
        with pytest.raises(TaskRegistryError, match='unavailable'):
            one.state(identifier)
        with pytest.raises(TaskRegistryError):
            one.create('sampling/createMessage', operation)
    finally:
        await one.close()
        await two.close()


async def test_close_revokes_all_siblings_before_waiting_and_canceled_caller_still_finishes_cleanup():
    registry = TaskRegistry()
    entered = [asyncio.Event() for _ in range(3)]
    cleanup = [asyncio.Event() for _ in range(3)]
    release = asyncio.Event()
    def operation(index):
        async def run(identifier):
            entered[index].set()
            try:
                await asyncio.sleep(30)
            finally:
                cleanup[index].set()
                await release.wait()
        return run
    keys = [registry.create('sampling/createMessage', operation(index))['task']['taskId'] for index in range(3)]
    closing = None
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        closing = asyncio.create_task(registry.close())
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleanup)), 2)
        assert not closing.done() and len(registry._tasks) == 3
        for key in keys:
            with pytest.raises(TaskRegistryError, match='unavailable'):
                registry.result(key)
        closing.cancel()
        await asyncio.sleep(.02)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 2)
        assert not registry._tasks and registry._retained_bytes == 0
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await registry.close()


async def test_automatic_ttl_stops_worker_without_polling_and_holds_capacity_until_finally():
    registry = TaskRegistry()
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        entered.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cleanup.set()
            await release.wait()
            return {'private': 'LATE RESULT'}
    identifier = registry.create('sampling/createMessage', operation, ttl=30)['task']['taskId']
    try:
        await entered.wait()
        # No reads/list/sweep drive this deadline.
        await asyncio.wait_for(cleanup.wait(), 2)
        assert identifier in registry._tasks and registry._expiring
        assert registry._tasks[identifier].cancel_requested
        with pytest.raises(TaskRegistryError, match='expired'):
            registry.result(identifier)
        release.set()
        await until(lambda: not registry._tasks and not registry._expiring)
        assert registry._retained_bytes == 0
    finally:
        release.set()
        await registry.close()


async def test_automatic_expiry_drops_completed_private_result_and_timer():
    registry = TaskRegistry()
    async def operation(identifier):
        return {'private': 'RETAINED'}
    identifier = registry.create('elicitation/create', operation, ttl=50)['task']['taskId']
    try:
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        assert registry._retained_bytes > 0
        await until(lambda: not registry._tasks and not registry._expiring)
        assert registry._retained_bytes == 0
        with pytest.raises(TaskRegistryError):
            registry.result(identifier)
    finally:
        await registry.close()


async def test_expiring_siblings_are_all_signaled_and_disconnect_waits_deadline_cleanup():
    registry = TaskRegistry()
    entered = [asyncio.Event(), asyncio.Event()]
    cleanup = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    def operation(index):
        async def run(identifier):
            entered[index].set()
            try:
                await asyncio.sleep(30)
            finally:
                cleanup[index].set()
                await release.wait()
            return {}
        return run
    for index in range(2):
        registry.create('sampling/createMessage', operation(index), ttl=40)
    closing = None
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleanup)), 2)
        assert len(registry._tasks) == 2 and len(registry._expiring) == 2
        closing = asyncio.create_task(registry.close())
        await asyncio.sleep(.01)
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 2)
        assert not registry._tasks and not registry._expiring
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        else:
            await registry.close()


async def test_manual_sweep_signals_all_expired_siblings_before_any_cleanup_wait():
    clock, registry = [0.0], TaskRegistry(clock=lambda: clock[0])
    entered = [asyncio.Event(), asyncio.Event()]
    cleanup = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    def operation(index):
        async def run(identifier):
            entered[index].set()
            try:
                await asyncio.sleep(30)
            finally:
                cleanup[index].set()
                await release.wait()
            return {}
        return run
    for index in range(2):
        registry.create('elicitation/create', operation(index), ttl=600000)
    sweep = None
    try:
        await asyncio.gather(*(event.wait() for event in entered))
        clock[0] = 1000
        sweep = asyncio.create_task(registry.sweep())
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleanup)), 2)
        assert not sweep.done() and len(registry._tasks) == 2
        release.set()
        await sweep
        assert not registry._tasks
    finally:
        release.set()
        if sweep is not None:
            await sweep
        await registry.close()


async def test_close_cancels_long_deadline_timer_before_return():
    registry = TaskRegistry()
    async def operation(identifier):
        return {'private': 'RETAINED'}
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    timer = registry._tasks[identifier].expiry
    assert timer is not None and not timer.cancelled()
    await registry.close()
    assert timer.cancelled() and not registry._tasks and not registry._expiring


async def test_timer_rechecks_injected_monotonic_clock_instead_of_expiring_early():
    clock = [0.0]
    registry = TaskRegistry(clock=lambda: clock[0])
    async def operation(identifier):
        return {}
    identifier = registry.create('elicitation/create', operation, ttl=20)['task']['taskId']
    try:
        await asyncio.sleep(.04)
        assert registry.state(identifier)['status'] == 'completed'
        assert registry._tasks[identifier].expiry is not None
        clock[0] = .1
        await until(lambda: not registry._tasks and not registry._expiring)
    finally:
        await registry.close()


async def test_automatic_expiry_full_capacity_reopens_only_after_all_actual_cleanup():
    registry = TaskRegistry()
    cleanup = [asyncio.Event() for _ in range(MAX_TASKS)]
    release = asyncio.Event()
    def operation(index):
        async def run(identifier):
            try:
                await asyncio.sleep(30)
            finally:
                cleanup[index].set()
                await release.wait()
            return {}
        return run
    try:
        for index in range(MAX_TASKS):
            registry.create('sampling/createMessage', operation(index), ttl=40)
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in cleanup)), 2)
        assert len(registry._tasks) == MAX_TASKS
        assert registry.list() == {'tasks': []}
        with pytest.raises(TaskRegistryError, match='capacity'):
            registry.create('sampling/createMessage', operation(0))
        release.set()
        await until(lambda: not registry._tasks and not registry._expiring)
        created = registry.create('elicitation/create', operation(0))
        assert created['task']['status'] == 'working'
    finally:
        release.set()
        await registry.close()
