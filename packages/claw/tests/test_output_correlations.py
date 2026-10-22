"""Correlation is scoped transport evidence, never untrusted source-label authority."""

from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputEnvelope, InputScope, ReplyTarget
from xdog.claw.core.outputs.store import OutputStore


def outbound(scope, identifier, *, trusted=True):
    return InputEnvelope(scope, SessionInput(identifier, 'automatic', 'message', 'Explicit outbound', (),
        {'reference': 'question-one'}), ReplyTarget('weixin', 'private-peer'), context={
            'kind': 'external', 'sender': 'private-peer', 'channel': 'weixin', 'timestamp': 0,
            'metadata': {'context_token': 'PRIVATE_TOKEN'}, **({'outbound_tool': 'message'} if trusted else {})})


async def test_correlation_reads_only_actual_current_group_tool_outputs_without_routing_data(tmp_path):
    store = await OutputStore.open(tmp_path / 'private/outputs.sqlite3')
    scope = InputScope('main', tmp_path)
    other = InputScope('other', tmp_path / 'other')
    try:
        assert (await store.delivery_for_reference(scope, 'question-one'))['status'] == 'untracked'
        # A forged canonical source/provenance reference cannot claim a tool notification.
        forged = await store.append(outbound(scope, 'forged', trusted=False), 'attempt', 0, 'channel', 'OTHER TEXT')
        begun = await store.begin(forged.id)
        await store.settle(forged.id, begun.send_id, 'delivered')
        assert (await store.delivery_for_reference(scope, 'question-one'))['status'] == 'untracked'
        row = await store.append(outbound(scope, 'actual'), 'attempt', 0, 'channel', 'PRIVATE_MESSAGE')
        assert (await store.delivery_for_reference(scope, 'question-one'))['status'] == 'pending'
        begun = await store.begin(row.id)
        await store.settle(row.id, begun.send_id, 'delivered')
        facts = await store.delivery_for_reference(scope, 'question-one')
        assert facts == {'status': 'delivered', 'outputs': [{'id': row.id, 'status': 'delivered'}]}
        assert (await store.delivery_for_reference(other, 'question-one'))['status'] == 'untracked'
        assert all(value not in str(facts) for value in ('PRIVATE_TOKEN', 'PRIVATE_MESSAGE', 'private-peer'))
        assert (await store.delivery_for_reference(scope, 'unrelated'))['status'] == 'untracked'
    finally:
        await store.close()


async def test_unknown_and_partial_are_not_reported_as_delivered(tmp_path):
    store = await OutputStore.open(tmp_path / 'private/outputs.sqlite3')
    scope = InputScope('main', tmp_path)
    try:
        first = await store.append(outbound(scope, 'one'), 'attempt', 0, 'channel', 'one')
        send = await store.begin(first.id)
        await store.settle(first.id, send.send_id, 'delivered')
        second = await store.append(outbound(scope, 'two'), 'attempt', 0, 'channel', 'two')
        assert (await store.delivery_for_reference(scope, 'question-one'))['status'] == 'partial'
        send = await store.begin(second.id)
        await store.settle(second.id, send.send_id, 'unknown', 'ACK lost')
        assert (await store.delivery_for_reference(scope, 'question-one'))['status'] == 'unknown'
        before = await store.records(scope)
        await store.delivery_for_reference(scope, 'question-one')
        assert await store.records(scope) == before
    finally:
        await store.close()


async def test_cancelled_correlated_output_is_not_unknown_or_delivered(tmp_path):
    store = await OutputStore.open(tmp_path / 'private/outputs.sqlite3')
    scope = InputScope('main', tmp_path)
    try:
        row = await store.append(outbound(scope, 'cancelled-call'), 'attempt', 0, 'channel', 'not sent')
        await store.cancel_pending(scope, ('cancelled-call',))
        result = await store.delivery_for_reference(scope, 'question-one')
        assert result == {'status': 'cancelled', 'outputs': [{'id': row.id, 'status': 'cancelled'}]}
    finally:
        await store.close()
