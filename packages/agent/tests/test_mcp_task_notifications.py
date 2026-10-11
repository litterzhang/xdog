"""Bounded public task status only; no telemetry-driven execution/cancellation/replay."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.mcp.reverse_requests import ReverseRequests
from xdog.agent.mcp.task_notifications import TaskNotifications
from xdog.agent.mcp.task_registry import TaskRegistry

from packages.agent.tests.test_mcp_sampling_owner import request, until


async def test_status_starts_after_successful_creation_delivery_and_contains_only_public_fields():
    registry, reverse, wire = TaskRegistry(), ReverseRequests(), []
    reverse.active, reverse.tasks = True, registry
    started, review, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def handler(value):
        pytest.fail('No synchronous downgrade')
    async def operation(value, identifier, original):
        original.authorize(identifier, 'sampling/createMessage')
        original.input_required(identifier, True)
        started.set()
        await review.wait()
        original.input_required(identifier, False)
        await release.wait()
        return {'model': 'offline', 'role': 'assistant', 'content': {'type': 'text', 'text': 'PRIVATE PAYLOAD'}}
    writing, delivery = asyncio.Event(), asyncio.Event()
    async def write(value):
        if 'id' in value:
            writing.set()
            await delivery.wait()
        wire.append(value)
    reverse.handler, reverse.sampling_task_handler = handler, operation
    source = request()
    source['params']['task'] = {}
    try:
        assert reverse.admit(source, write, timeout=2)
        await asyncio.wait_for(writing.wait(), 2)
        await asyncio.wait_for(started.wait(), 2)
        assert not wire and not reverse.status._jobs
        delivery.set()
        await until(lambda: len(wire) >= 2)
        identifier = wire[0]['result']['task']['taskId']
        assert wire[1]['method'] == 'notifications/tasks/status'
        assert wire[1]['params']['status'] == 'input_required'
        review.set()
        await until(lambda: any(row.get('params', {}).get('status') == 'working' for row in wire))
        release.set()
        await until(lambda: any(row.get('params', {}).get('status') == 'completed' for row in wire))
        await until(lambda: not reverse.status._jobs)
        notices = [row for row in wire if 'method' in row]
        for row in notices:
            assert set(row) == {'jsonrpc', 'method', 'params'}
            assert set(row['params']) == {'taskId', 'status', 'ttl', 'createdAt', 'lastUpdatedAt',
                                          'pollInterval', '_meta'}
            assert row['params']['_meta'] == {'io.modelcontextprotocol/related-task': {'taskId': identifier}}
            assert row['params']['taskId'] == identifier
        assert 'PRIVATE' not in json.dumps(notices)
        assert registry.result(identifier)['content']['text'] == 'PRIVATE PAYLOAD'
    finally:
        delivery.set()
        review.set()
        release.set()
        await reverse.close()
    assert not reverse.status._jobs and not registry._tasks


async def test_slow_status_coalesces_and_does_not_block_task_get_or_result():
    registry, notifications, sent = TaskRegistry(), TaskNotifications(), []
    release, entered = asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        await release.wait()
        return {'private': 'REVIEWED'}
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    async def stalled(value):
        entered.set()
        await asyncio.sleep(30)
        sent.append(value)
    try:
        notifications.start(registry, identifier, 'sampling/createMessage', stalled, lambda: True, .06)
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(100):
            registry.input_required(identifier, True)
            registry.input_required(identifier, False)
        assert len(notifications._jobs) == 1
        release.set()
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        assert registry.result(identifier) == {'private': 'REVIEWED'}
        await until(lambda: not notifications._jobs)
        assert not sent and registry._tasks[identifier].observer is None
    finally:
        release.set()
        await notifications.close()
        await registry.close()


async def test_status_observer_exception_cannot_turn_completed_operation_into_failed():
    registry = TaskRegistry()
    release = asyncio.Event()
    async def operation(identifier):
        await release.wait()
        return {'private': 'REVIEWED'}
    identifier = registry.create('elicitation/create', operation)['task']['taskId']
    def broken(state):
        raise OSError('Private observer failure')
    try:
        registry.watch(identifier, broken)
        assert registry._tasks[identifier].observer is None
        release.set()
        await until(lambda: registry.state(identifier)['status'] == 'completed')
        assert registry.result(identifier) == {'private': 'REVIEWED'}
    finally:
        release.set()
        await registry.close()


async def test_status_family_withdrawal_and_expiry_leave_no_monitor_or_late_notice():
    registry, notifications, sent = TaskRegistry(), TaskNotifications(), []
    release = asyncio.Event()
    async def operation(identifier):
        await release.wait()
        return {}
    async def write(row):
        sent.append(row)
    one = registry.create('sampling/createMessage', operation)['task']['taskId']
    two = registry.create('elicitation/create', operation, ttl=100)['task']['taskId']
    try:
        notifications.start(registry, one, 'sampling/createMessage', write, lambda: True, 1)
        notifications.start(registry, two, 'elicitation/create', write, lambda: True, 1)
        await until(lambda: len(sent) == 2)
        await notifications.close('sampling/createMessage')
        await until(lambda: one not in notifications._jobs)
        assert two in notifications._jobs
        registry.input_required(one, True)
        await until(lambda: two not in registry._tasks and not notifications._jobs)
        assert len(sent) == 2  # No stale status for withdrawn or expired IDs.
    finally:
        release.set()
        await notifications.close()
        await registry.close()


async def test_cancel_before_first_step_notifies_terminal_without_private_operation():
    registry, notifications, sent, calls = TaskRegistry(), TaskNotifications(), [], []
    async def operation(identifier):
        calls.append(identifier)
        return {'private': 'NEVER RUN'}
    async def write(row):
        sent.append(row)
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    try:
        notifications.start(registry, identifier, 'sampling/createMessage', write, lambda: True, 1)
        await registry.cancel(identifier)
        await until(lambda: any(row['params']['status'] == 'cancelled' for row in sent))
        await until(lambda: not notifications._jobs)
        assert not calls and all(row['params']['taskId'] == identifier for row in sent)
        assert 'PRIVATE' not in json.dumps(sent)
    finally:
        await notifications.close()
        await registry.close()


async def test_notification_close_waits_actual_writer_cleanup_and_never_releases_late_notice():
    registry, notifications, sent = TaskRegistry(), TaskNotifications(), []
    entered, cleanup, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(identifier):
        await asyncio.sleep(30)
        return {}
    async def write(row):
        entered.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleanup.set()
            await release.wait()
        sent.append(row)
    identifier = registry.create('sampling/createMessage', operation)['task']['taskId']
    closing = None
    try:
        notifications.start(registry, identifier, 'sampling/createMessage', write, lambda: True, 1)
        await asyncio.wait_for(entered.wait(), 2)
        closing = asyncio.create_task(notifications.close())
        await asyncio.wait_for(cleanup.wait(), 2)
        closing.cancel()
        await asyncio.sleep(.01)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 2)
        await until(lambda: not notifications._jobs)
        assert not sent and registry._tasks[identifier].observer is None
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await notifications.close()
        await registry.close()
