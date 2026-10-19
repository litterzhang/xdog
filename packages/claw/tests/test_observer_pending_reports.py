"""A known unavailable destination is queued output, not an attempted send."""
from __future__ import annotations

from xdog.claw.core.inputs.models import InputScope

from packages.claw.tests.eyes_helpers import managed as managed


async def test_finished_source_may_release_cursor_while_original_report_stays_durably_pending(managed):
    created = await managed.create()
    reference = (await managed.record(created['id'])).reply
    managed.orch.unregister_reply_channel(reference.binding)
    managed.orch._channels.clear()
    managed.clock.advance(240)
    saved = await managed.completed(created['id'], pending_report=True)
    # Source state is not the report outbox: a terminal, accepted invocation
    # never needs replay merely because its original recipient is offline.
    record = await managed.record(created['id'])
    assert record.state['pending'] is None
    assert not saved['error']
    assert saved['occurrences'][0]['delivery_status'] == 'pending'
    assert saved['occurrences'][0]['execution_status'] == 'succeeded'
    managed.channel.send_message.assert_not_awaited()


async def test_known_offline_report_does_not_publish_sending_during_reconciliation(managed):
    created = await managed.create()
    reference = (await managed.record(created['id'])).reply
    managed.orch.unregister_reply_channel(reference.binding)
    managed.orch._channels.clear()
    managed.clock.advance(240)
    saved = await managed.completed(created['id'], pending_report=True)
    message = saved['occurrences'][0]['message_id']
    scope = InputScope('g1', managed.orch.get_group_workspace('g1'))
    receipt = await managed.manager.inputs.store.get(scope, message)
    outputs = await managed.orch.output_service()
    before = await outputs.store.records(scope)
    await managed.manager._report(receipt)
    after = await outputs.store.records(scope)
    assert after == before
    assert all(row.status == 'pending' and not row.send_id for row in after)
    assert (await managed.manager.store.occurrence(receipt))['delivery_status'] == 'pending'
    assert managed.model.calls == ['report']
