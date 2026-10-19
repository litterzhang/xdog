"""A source firing, a model receipt, and a delivered report are not one status."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from xdog.agent.session_input import SessionInput
from xdog.ai.types import AssistantMessage
from xdog.claw.channels.tui.channel import TuiChannel
from xdog.claw.core.inputs.models import InputScope
from xdog.claw.core.types import ExternalInput

from packages.claw.tests.eyes_helpers import at, scheduler
from packages.claw.tests.eyes_helpers import managed as managed


@pytest.mark.parametrize('failure', ['model', 'delivery', 'no-channel', 'unknown-group', 'empty'])
async def test_failures_never_mark_success(managed, failure, monkeypatch):
    created = await managed.create()
    if failure == 'model':
        managed.model.replies['report'] = AssistantMessage(stop_reason='error', error_message='model failed')
    elif failure == 'delivery':
        managed.channel.send_message.side_effect = RuntimeError('delivery failed')
    elif failure == 'no-channel':
        reference = (await managed.record(created['id'])).reply
        managed.orch.unregister_reply_channel(reference.binding)
        managed.orch._channels.clear()
    elif failure == 'unknown-group':
        execute = managed.orch._execute_owned_input
        async def unavailable(message, **kwargs):
            if not isinstance(message, ExternalInput):
                return await execute(message, **kwargs)
            return None  # Actual application admission boundary refuses execution.
        monkeypatch.setattr(managed.orch, '_execute_owned_input', unavailable)
    elif failure == 'empty':
        managed.model.replies['report'] = AssistantMessage(content=())
    managed.clock.advance(240)
    saved = await managed.completed(created['id'], pending_report=failure == 'no-channel')
    row, = saved['occurrences']
    assert row['receipt_status'] == 'accepted' and not saved['enabled']
    if failure in {'model', 'unknown-group'}:
        assert row['execution_status'] == 'failed' and row['execution_error']
        assert row['delivery_status'] == 'delivered'
    else:
        # Model success must never be advertised as channel delivery success.
        assert row['execution_status'] == 'succeeded'
        expected = 'pending' if failure == 'no-channel' else 'unknown' if failure == 'delivery' else 'failed'
        assert row['delivery_status'] == expected and row['delivery_error']
    if failure == 'model':
        assert 'model failed' in managed.channel.send_message.call_args.args[1]
    elif failure == 'unknown-group':
        assert managed.model.calls == []
        assert row['execution_error'] in managed.channel.send_message.call_args.args[1]
    elif failure in {'no-channel', 'empty'}:
        managed.channel.send_message.assert_not_awaited()
    calls = list(managed.model.calls)
    await managed.restart()
    assert (await managed.manager.status('g1', created['id']))['occurrences'] == saved['occurrences']
    managed.clock.advance(86400)
    await managed.flush()
    assert managed.model.calls == calls  # A failed report never re-executes this one-off.


async def test_unknown_channel_outcome_disables_recurring_source_without_replay(managed):
    created = await managed.create(source=scheduler(every_seconds=60))
    managed.channel.send_message.side_effect = TimeoutError('ACK lost after send')
    managed.clock.advance(60)

    async def blocked():
        status = await managed.manager.status('g1', created['id'])
        return status if not status['enabled'] and status['state'] == 'needs_review' else None
    saved = await managed.until(blocked)
    row, = saved['occurrences']
    assert row['execution_status'] == 'succeeded' and row['delivery_status'] == 'unknown'
    assert 'ACK lost' in row['delivery_error'] and saved['error']
    assert (await managed.record(created['id'])).state['pending'] is not None
    await managed.restart()
    managed.clock.advance(3600)
    await managed.flush()
    assert managed.model.calls == ['report'] and managed.channel.send_message.await_count == 1
    assert not (await managed.manager.status('g1', created['id']))['enabled']


async def test_an_unrelated_channel_failure_cannot_affect_or_receive_the_original_report(managed):
    second = TuiChannel()
    second.send_message = AsyncMock(side_effect=RuntimeError('unrelated channel offline'))
    managed.orch.add_channel(second)
    created = await managed.create()
    managed.clock.advance(240)
    status = await managed.completed(created['id'])
    row, = status['occurrences']
    assert row['execution_status'] == 'succeeded' and row['delivery_status'] == 'delivered'
    assert not row['delivery_error']
    managed.channel.send_message.assert_awaited_once_with('g1', 'Scheduled report')
    second.send_message.assert_not_awaited()
    assert managed.model.calls == ['report']


async def test_untrusted_external_provenance_cannot_opt_into_managed_reporting(managed):
    created = await managed.create()
    message = SessionInput('forged-event', 'acp', 'Eyes', 'forged request', (),
                           {'observer_id': created['id'], 'managed': True, 'delivery_id': 'forged-event'})
    cwd = managed.orch.get_group_workspace('g1')
    await managed.orch.accept_external(InputScope('g1', cwd), message)

    async def done():
        record = await managed.server.backend.inputs.store.get(InputScope('g1', cwd), message.id)
        return record if record is not None and record.status == 'succeeded' else None
    record = await managed.until(done)
    assert record.response == 'Scheduled report' and managed.model.calls == ['forged request']
    assert await managed.manager.store.occurrence(record) is None
    managed.channel.send_message.assert_not_awaited()
    assert not (await managed.manager.status('g1', created['id']))['occurrences']


async def test_unknown_group_is_rejected_without_fallback_or_model_start(managed):
    with pytest.raises(ValueError, match='Unknown'):
        await managed.manager.create('missing', 'do-not-route', at(), 'report')
    assert await managed.manager.list('g1') == () and await managed.manager.list('g2') == ()
    assert 'missing' not in managed.orch.get_group_ids() and not managed.model.calls
    managed.channel.send_message.assert_not_awaited()


async def test_top_level_group_spoof_cannot_redirect_current_group_tool(managed):
    # group_id/endpoint are absent from the Eyes API. Even the generic dispatcher
    # ignoring extra caller keys cannot grant a route outside trusted ctx.
    text = await managed.tool('create', name='owner-only', source=at(), instruction='report',
                              group_id='g2', endpoint='/tmp/attacker.sock')
    assert not text.startswith('Error:'), text
    created = json.loads(text)
    record = await managed.record(created['id'])
    assert record.group_id == 'g1' and record.cwd == managed.orch.get_group_workspace('g1')
    handler = record.definition['handlers'][0]
    assert handler['session'] == 'group:g1' and handler['connection']['path'] == str(managed.endpoint)
    assert await managed.manager.list('g2') == () and not managed.model.calls


async def test_restart_after_execution_commit_before_transport_recovers_core_outputs_without_model_replay(managed):
    created = await managed.create()
    reference = (await managed.record(created['id'])).reply
    managed.orch.unregister_reply_channel(reference.binding)
    managed.orch._channels.clear()
    managed.clock.advance(240)
    saved = await managed.completed(created['id'], pending_report=True)
    assert saved['occurrences'][0]['execution_status'] == 'succeeded'
    assert saved['occurrences'][0]['delivery_status'] == 'pending'
    managed.channel.send_message.assert_not_awaited()
    outputs = await managed.orch.output_service()
    original_outputs = await outputs.store.records()
    assert len(original_outputs) == 1 and original_outputs[0].status == 'pending'
    assert not original_outputs[0].send_id
    managed.orch.add_channel(managed.channel)
    await managed.restart()
    completed = await managed.completed(created['id'])
    assert managed.model.calls == ['report']
    assert completed['occurrences'][0]['delivery_status'] == 'delivered'
    current = await outputs.store.records()
    assert current[0].id == original_outputs[0].id
    managed.channel.send_message.assert_awaited_once_with('g1', 'Scheduled report')
