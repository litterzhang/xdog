"""Real disk intake/attempt receipts, without an ACP receiver or Agent runtime."""
from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from xdog.agent.session_input import SessionInput
from xdog.claw.core.inputs.models import InputEnvelope, InputScope, ReplyTarget


def envelope(tmp_path, identifier='one', group='main', **changes):
    return InputEnvelope(InputScope(group, tmp_path),
        SessionInput(identifier, 'human', 'TUI', 'Check deployment', (), {}),
        **{'reply': ReplyTarget('tui', 'original-peer'), 'human_authorized': True, **changes})


async def test_ack_and_dedup_survive_reopen_without_agent_or_protocol(tmp_path):
    from xdog.claw.core.inputs.store import InputStore
    path = tmp_path / 'private' / 'input.sqlite3'
    value = envelope(tmp_path)
    store = await InputStore.open(path)
    first, duplicate = await store.enqueue(value)
    assert first.status == 'pending' and duplicate is False
    await store.close()
    store = await InputStore.open(path)
    try:
        existing, duplicate = await store.enqueue(value)
        assert duplicate is True and existing == first
        assert (await store.get(value.scope, 'one')).envelope.reply.recipient == 'original-peer'
        assert await store.get(value.scope, 'absent') is None
    finally:
        await store.close()


@pytest.mark.parametrize('change', ['reply', 'authority', 'instruction', 'admission'])
async def test_same_message_id_cannot_alias_different_host_or_content(tmp_path, change):
    from xdog.claw.core.inputs.store import InputStore
    value = envelope(tmp_path)
    if change == 'instruction':
        different = InputEnvelope(value.scope, replace(value.message, instruction='different'),
                                  reply=value.reply, human_authorized=True)
    else:
        overrides = {'reply': ReplyTarget('weixin', 'somebody-else')} if change == 'reply' else (
            {'human_authorized': False} if change == 'authority' else {'admission_key': 'different-binding'})
        different = envelope(tmp_path, **overrides)
    store = await InputStore.open(tmp_path / 'private' / 'input.sqlite3')
    try:
        await store.enqueue(value)
        with pytest.raises(ValueError, match='different'):
            await store.enqueue(different)
        assert (await store.get(value.scope, 'one')).envelope == value
    finally:
        await store.close()


async def test_scope_isolation_and_one_claimed_or_running_attempt_per_group(tmp_path):
    from xdog.claw.core.inputs.store import InputStore
    store = await InputStore.open(tmp_path / 'private' / 'input.sqlite3')
    try:
        one, two, other = envelope(tmp_path), envelope(tmp_path, 'two'), envelope(tmp_path, group='other')
        for value in (one, two, other):
            await store.enqueue(value)
        claimed = await store.claim(one.scope)
        assert claimed.status == 'claimed' and claimed.attempt_id
        assert await store.claim(one.scope) is None
        assert (await store.claim(other.scope)).envelope.message.id == 'one'
        with pytest.raises(ValueError):
            await store.finish(claimed, 'succeeded')
        running = await store.start(claimed, session_id='actual-context')
        assert running.status == 'running'
        assert await store.claim(one.scope) is None
        result = await store.finish(running, 'succeeded', response='done')
        assert result.response == 'done' and result.session_id == 'actual-context'
        assert (await store.claim(one.scope)).envelope.message.id == 'two'
        assert await store.get(InputScope('missing', tmp_path), 'one') is None
    finally:
        await store.close()


async def test_full_inbox_refuses_new_input_but_preserves_existing_ack_and_order(tmp_path):
    from xdog.claw.core.inputs.store import InputStore
    store = await InputStore.open(tmp_path / 'private' / 'input.sqlite3', max_pending=2)
    try:
        one, two = envelope(tmp_path), envelope(tmp_path, 'two')
        await store.enqueue(one)
        await store.enqueue(two)
        with pytest.raises(ValueError, match='capacity'):
            await store.enqueue(envelope(tmp_path, 'third'))
        assert (await store.enqueue(one))[1] is True
        assert [r.envelope.message.id for r in await store.records(one.scope)] == ['one', 'two']
        running = await store.start(await store.claim(one.scope))
        with pytest.raises(ValueError, match='capacity'):
            await store.enqueue(envelope(tmp_path, 'third'))
        await store.finish(running, 'succeeded')
        assert (await store.enqueue(envelope(tmp_path, 'third')))[0].status == 'pending'
    finally:
        await store.close()


async def test_explicit_unstarted_cancel_is_durable_and_does_not_cancel_another_run(tmp_path):
    from xdog.claw.core.inputs.store import InputStore
    store = await InputStore.open(tmp_path / 'private' / 'input.sqlite3')
    try:
        value = envelope(tmp_path)
        await store.enqueue(value)
        claimed = await store.claim(value.scope)
        cancelled = await store.cancel(value.scope, 'one')
        assert cancelled.status == 'cancelled'
        assert await store.cancel(value.scope, 'one') == cancelled
        with pytest.raises(ValueError):
            await store.start(claimed)
        await store.enqueue(envelope(tmp_path, 'two'))
        running = await store.start(await store.claim(value.scope))
        with pytest.raises(ValueError, match='running'):
            await store.cancel(value.scope, 'two')
        assert (await store.get(value.scope, 'two')).status == 'running'
        await store.finish(running, 'succeeded')
    finally:
        await store.close()


async def test_retry_makes_new_attempt_without_erasing_failed_attempt_or_allowing_stale_finish(tmp_path):
    from xdog.claw.core.inputs.store import InputStore
    store = await InputStore.open(tmp_path / 'private' / 'input.sqlite3')
    try:
        value = envelope(tmp_path)
        await store.enqueue(value)
        old = await store.start(await store.claim(value.scope))
        await store.finish(old, 'failed', error='inspect effects')
        assert (await store.enqueue(value))[0].status == 'failed'  # Duplicate is never a retry.
        await store.retry(value.scope, 'one')
        current = await store.start(await store.claim(value.scope))
        assert current.attempt_id != old.attempt_id
        with pytest.raises(ValueError, match='attempt'):
            await store.finish(old, 'succeeded')
        await store.finish(current, 'succeeded', response='second result')
        attempts = await store.attempts(value.scope, 'one')
        assert [r.status for r in attempts] == ['failed', 'succeeded']
        assert attempts[0].error == 'inspect effects'
        assert attempts[1].response == 'second result'
        with pytest.raises(ValueError):
            await store.retry(value.scope, 'one')
    finally:
        await store.close()


async def test_recovery_distinguishes_claimed_from_started_and_fences_old_claim(tmp_path):
    from xdog.claw.core.inputs.store import InputStore
    path = tmp_path / 'private' / 'input.sqlite3'
    claimed_value, running_value = envelope(tmp_path), envelope(tmp_path, group='other')
    store = await InputStore.open(path)
    await store.enqueue(claimed_value)
    old_claim = await store.claim(claimed_value.scope)
    await store.enqueue(running_value)
    old_run = await store.start(await store.claim(running_value.scope))
    await store.close()
    store = await InputStore.open(path)
    try:
        assert (await store.get(claimed_value.scope, 'one')).status == 'pending'
        assert (await store.get(running_value.scope, 'one')).status == 'interrupted'
        assert await store.claim(running_value.scope) is None
        current = await store.claim(claimed_value.scope)
        assert current.attempt_id != old_claim.attempt_id
        with pytest.raises(ValueError, match='attempt'):
            await store.start(old_claim)
        with pytest.raises(ValueError, match='attempt'):
            await store.finish(old_run, 'succeeded')
        assert [a.status for a in await store.attempts(claimed_value.scope, 'one')] == ['released', 'claimed']
        assert [a.status for a in await store.attempts(running_value.scope, 'one')] == ['interrupted']
    finally:
        await store.close()


async def test_concurrent_admission_does_not_overfill_or_delete_accepted_messages(tmp_path):
    from xdog.claw.core.inputs.store import InputStore
    store = await InputStore.open(tmp_path / 'private' / 'input.sqlite3', max_pending=2)
    try:
        results = await asyncio.gather(*(store.enqueue(envelope(tmp_path, str(i))) for i in range(10)),
                                       return_exceptions=True)
        assert sum(isinstance(result, tuple) for result in results) == 2
        assert sum(isinstance(result, ValueError) for result in results) == 8
        assert [r.envelope.message.id for r in await store.records(InputScope('main', tmp_path))] == ['0', '1']
    finally:
        await store.close()
