"""Real Eyes scheduler → ACP → leased group inbox → actual Claw agent → report."""
from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from xdog.ai.types import AssistantMessage, DoneEvent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.acp.server import ClawAcpServer
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.config import ClawConfig
from xdog.claw.core.observers.manager import ManagedObservers
from xdog.claw.core.runtime.orchestrator import Orchestrator
from xdog.claw.core.types import Group, GroupConfig
from xdog.eyes.managed_scheduler import SchedulerClocks


async def eventually(check):
    async with asyncio.timeout(5):
        while not await check():
            await asyncio.sleep(.005)


@pytest.fixture
async def managed(tmp_path):
    clock, calls = [2_000_000_000.0], []
    gate, entered = asyncio.Event(), asyncio.Event()
    gate.set()

    def model(name, context, options=None):
        calls.append(context)
        response = AssistantMessage(content=(TextContent(text='Managed report'),))
        future = asyncio.get_running_loop().create_future()
        future.set_result(response)
        async def events():
            entered.set()
            await gate.wait()
            yield StartEvent(partial=response)
            yield DoneEvent(message=response)
        return EventStream.from_async_generator(events(), future)

    async def sleep(seconds):
        await asyncio.sleep(.005)

    config = ClawConfig(data_dir=str(tmp_path / 'data'), enabled_tools=('eyes',), eyes_enabled=True)
    orch = Orchestrator(config, model='test/managed', stream_fn=model)
    for group in ('main', 'other'):
        orch.register_group(Group(id=group, workspace=str(tmp_path), enabled_tools=('eyes',),
                                  config=GroupConfig(debounce_ms=0)))
    channel = TuiChannel()
    channel.send_message = AsyncMock()
    orch.add_channel(channel)
    with tempfile.TemporaryDirectory(prefix='claw-managed-') as short:
        server = await ClawAcpServer.start(orch, Path(short) / 'acp.sock', tmp_path / 'private' / 'inbox.sqlite3')
        service = ManagedObservers(server.backend, server.path,
            clocks=SchedulerClocks(lambda: clock[0], lambda: clock[0], sleep))
        orch.set_observers(service)
        await service.start()
        try:
            yield service, orch, server, clock, calls, channel, gate, entered
        finally:
            gate.set()
            await service.close()
            await server.close()
            await orch.stop()


def at(clock, delta=10):
    return {'type': 'scheduler', 'schedule': {
        'at': datetime.fromtimestamp(clock[0] + delta, UTC).isoformat()}}


async def test_one_off_create_receipt_execution_and_report_are_separate(managed):
    service, _, _, clock, calls, channel, _, _ = managed
    record = await service.create('main', 'report', at(clock), 'Summarize progress')
    assert not calls and not record['occurrences']
    clock[0] += 10
    async def completed():
        status = await service.status('main', record['id'])
        return status['state'] == 'completed'
    await eventually(completed)
    result = await service.status('main', record['id'])
    assert len(calls) == 1 and not result['enabled']
    history = result['occurrences']
    assert history[0]['receipt_status'] == 'accepted'
    assert history[0]['execution_status'] == 'succeeded'
    assert history[0]['delivery_status'] == 'delivered'
    channel.send_message.assert_awaited_once_with('main', 'Managed report')
    clock[0] += 365 * 86400
    await asyncio.sleep(.03)
    assert len(calls) == 1


async def test_cancel_only_current_group_and_does_not_rearm_same_name(managed):
    service, _, _, clock, calls, channel, _, _ = managed
    record = await service.create('main', 'report', at(clock), 'Report')
    with pytest.raises(ValueError, match='Unknown observer'):
        await service.cancel('other', record['id'])
    with pytest.raises(ValueError, match='Unknown observer'):
        await service.status('other', record['id'])
    assert await service.list('other') == ()
    await service.cancel('main', record['id'])
    assert not (await service.create('main', 'report', at(clock), 'Report'))['enabled']
    clock[0] += 10
    await asyncio.sleep(.03)
    assert not calls
    channel.send_message.assert_not_awaited()


@pytest.mark.parametrize('failure', ['channel', 'no-channel', 'timeout'])
async def test_report_failure_does_not_rerun_agent(managed, failure):
    service, orch, _, clock, calls, channel, _, _ = managed
    if failure == 'no-channel':
        orch._channels.clear()
    else:
        channel.send_message.side_effect = (TimeoutError('unknown send') if failure == 'timeout'
                                            else RuntimeError('rejected'))
    record = await service.create('main', 'report', at(clock), 'Report')
    clock[0] += 10
    async def terminal():
        row = await service.status('main', record['id'])
        return bool(row['occurrences']) and row['occurrences'][0]['delivery_status'] in {'failed', 'unknown'}
    await eventually(terminal)
    row = await service.status('main', record['id'])
    assert len(calls) == 1 and row['occurrences'][0]['execution_status'] == 'succeeded'
    clock[0] += 1000
    await asyncio.sleep(.05)
    assert len(calls) == 1
    assert channel.send_message.await_count <= 1


async def test_busy_interval_skips_ticks_without_overlapping(managed):
    service, _, _, clock, calls, channel, gate, entered = managed
    gate.clear()
    record = await service.create('main', 'periodic',
                                  {'type': 'scheduler', 'schedule': {'every_seconds': 10}}, 'Report')
    clock[0] += 10
    await asyncio.wait_for(entered.wait(), 3)
    assert len(calls) == 1
    clock[0] += 500
    await asyncio.sleep(.04)
    assert len(calls) == 1
    gate.set()
    async def delivered():
        row = await service.status('main', record['id'])
        return row['occurrences'][0]['delivery_status'] == 'delivered'
    await eventually(delivered)
    await asyncio.sleep(.04)
    assert len(calls) == 1
    clock[0] += 10
    async def second():
        return len(calls) == 2
    await eventually(second)
    await service.cancel('main', record['id'])


async def test_namespace_change_never_silently_retargets_saved_work(managed):
    service, _, server, clock, calls, _, _, _ = managed
    record = await service.create('main', 'report', at(clock), 'Report')
    await server.backend.inbox._disk(lambda db: db.execute(
        "UPDATE scopes SET namespace='replaced' WHERE group_id='main'"))
    clock[0] += 10
    async def paused():
        row = await service.status('main', record['id'])
        return row['state'] == 'needs_review'
    await eventually(paused)
    assert not calls


async def test_lost_enqueue_ack_retains_original_identity_and_never_repeats_model(managed, monkeypatch):
    service, _, server, clock, calls, channel, gate, entered = managed
    gate.clear()
    created = await service.create('main', 'lost-ack', at(clock), 'Report')
    handler = service._handlers[created['id']]
    original = handler.client.enqueue
    sent = []
    async def lose_ack(parameters):
        sent.append(parameters.copy())
        await original(parameters)
        raise ConnectionError('ACK lost after committed intake')
    monkeypatch.setattr(handler.client, 'enqueue', lose_ack)
    clock[0] += 10
    await asyncio.wait_for(entered.wait(), 3)
    assert len(calls) == 1
    row = await service.store.get('main', service._cwd('main'), created['id'])
    assert row.state['pending']['id'] == sent[0]['message_id']
    receipt = await server.backend.inbox.get('main', row.cwd, sent[0]['message_id'])
    assert receipt.status == 'running'
    gate.set()
    async def completed():
        return (await service.status('main', created['id']))['state'] == 'completed'
    await eventually(completed)
    assert len(sent) == 1 and len(calls) == 1
    channel.send_message.assert_awaited_once()


async def test_at_admitted_before_slow_connection_keeps_original_deadline(managed, monkeypatch):
    service, _, _, clock, calls, _, _, _ = managed
    original = service._handler
    async def delayed(record, definition):
        clock[0] += 30
        return await original(record, definition)
    monkeypatch.setattr(service, '_handler', delayed)
    created = await service.create('main', 'slow-connect', at(clock, 10), 'Report')
    async def completed():
        return (await service.status('main', created['id']))['state'] == 'completed'
    await eventually(completed)
    assert len(calls) == 1


async def test_file_observer_is_explicit_and_not_a_crash_restored_timer(managed):
    service, _, _, _, _, _, _, _ = managed
    root = service._cwd('main')
    (root / 'src').mkdir()
    created = await service.create('main', 'watch', {'type': 'file', 'root': '.', 'include': ['src/**']}, 'Review')
    record = await service.store.get('main', root, created['id'])
    assert record.recovery == 'ephemeral' and created['id'] in service._files
    await service.cancel('main', created['id'])
    assert created['id'] not in service._files


async def test_explicit_file_observer_closes_every_started_handler(managed, monkeypatch):
    from xdog.eyes.handlers.acp import AcpHandler
    service, _, _, _, _, _, _, _ = managed
    original, handlers = AcpHandler.start, []
    async def capture(handler):
        await original(handler)
        handlers.append(handler)
    monkeypatch.setattr(AcpHandler, 'start', capture)
    created = await service.create('main', 'file-resource', {'type': 'file', 'root': '.',
                                   'include': ['src/**']}, 'Review')
    await service.cancel('main', created['id'])
    await service.close()
    assert handlers and all(handler._closed for handler in handlers)


async def test_checkpoint_and_error_record_write_failure_halts_visibly(managed, monkeypatch):
    service, _, _, clock, calls, _, _, _ = managed
    release = asyncio.Event()
    async def failed_save(*args):
        await release.wait()
        raise OSError('checkpoint disk failure')
    async def failed_error(*args, **kwargs):
        raise OSError('error record also unavailable')
    monkeypatch.setattr(service, '_save', failed_save)
    monkeypatch.setattr(service.store, 'fail', failed_error)
    created = await service.create('main', 'disk-failure', at(clock), 'Report')
    release.set()
    async def stopped():
        return created['id'] not in service._tasks
    await eventually(stopped)
    result = await service.status('main', created['id'])
    assert result['state'] == 'failed' and 'checkpoint disk failure' in result['error']
    assert not calls and not result['occurrences']


async def test_integer_host_clock_preserves_exact_canonical_outbox_payload(managed):
    from dataclasses import replace
    service, _, _, clock, calls, _, _, _ = managed
    service.clocks = replace(service.clocks, wall=lambda: int(clock[0]), monotonic=lambda: int(clock[0]))
    created = await service.create('main', 'integer-clock', at(clock), 'Report')
    clock[0] += 10
    async def completed():
        return (await service.status('main', created['id']))['state'] == 'completed'
    await eventually(completed)
    assert len(calls) == 1
