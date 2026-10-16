"""Actual owning Agent queue two-phase returns, with no implicit removal or model invocation."""
from __future__ import annotations

import asyncio
import base64
import json
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSupervisor
from xdog.ai.types import AssistantMessage, ImageContent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


async def setup():
    entered = threading.Event()
    calls = []
    def stream(*args):
        calls.append(args)
        async def events():
            entered.set()
            yield StartEvent(partial=AssistantMessage(content=(TextContent(text='HELD'),)))
            await asyncio.sleep(60)
        return EventStream.from_async_generator(events())
    owner = ChildSupervisor(lambda spec, publish: Agent(stream, config=AgentConfig(model='offline/model')))
    child = await owner.start('INITIAL')
    assert await asyncio.to_thread(entered.wait, 3)
    return owner, child, calls


async def queue(owner, child, action='list', lease='native-owner', **fields):
    return await owner.control('queue', child, json.dumps({'action': action, **fields}), return_owner=lease)


@pytest.mark.parametrize('size', [68, 10 * 1024 * 1024])
async def test_actual_two_phase_image_return_preserves_input_until_verified_commit_and_replay_is_once(size):
    owner, child, calls = await setup()
    body = base64.b64decode(PNG) + b'\0' * (size - 68)
    image = ImageContent(data=base64.b64encode(body).decode(), mime_type='image/png')
    try:
        await owner.control('steer', child, 'RETURN-PHOTO /quit literal', images=(image,))
        before = (await queue(owner, child))['queue']
        row = before['inputs'][0]
        prepared = await queue(owner, child, 'return_prepare', id=row['id'], revision=before['revision'])
        assert prepared['queue']['return_prepared'] == row
        assert prepared['image_recovery']['images'][0]['data'] == image.data
        assert (await queue(owner, child))['queue'] == before  # No removal on bytes/read receipt alone.
        command = {'id': row['id'], 'revision': before['revision'],
                   'token': prepared['queue']['return_token'], 'receipt': prepared['queue']['image_receipt']}
        committed = await queue(owner, child, 'return_commit', **command)
        assert committed['queue']['returned'] == row and not committed['queue']['inputs']
        assert (await queue(owner, child, 'return_commit', **command)) == committed
        assert len(calls) == 1 and len(owner._children[child].agent.messages) == 1
    finally:
        await owner.aclose()


@pytest.mark.parametrize('fault', ['owner', 'receipt', 'revision', 'selected', 'edited', 'cancel',
                                 'expired', 'stopped'])
async def test_return_stale_foreign_cancelled_or_unverified_receipts_do_not_remove_input(fault, monkeypatch):
    import xdog.agent.supervision_return as module
    owner, child, calls = await setup()
    image = ImageContent(data=PNG, mime_type='image/png')
    try:
        await owner.control('follow-up', child, 'RETAIN ORIGINAL', images=(image,))
        before = (await queue(owner, child))['queue']
        row = before['inputs'][0]
        prepared = await queue(owner, child, 'return_prepare', id=row['id'], revision=before['revision'])
        command = {'id': row['id'], 'revision': before['revision'], 'token': prepared['queue']['return_token'],
                   'receipt': prepared['queue']['image_receipt']}
        lease = 'native-owner'
        if fault == 'owner':
            lease = 'different-owner'
        elif fault == 'receipt':
            command['receipt'] = {**command['receipt'], 'content': 'WRONG TEXT'}
        elif fault == 'revision':
            command['revision'] = '0' * 64
        elif fault == 'selected':
            async def select():
                owner._children[child].agent._dequeue_follow_ups()
            await owner._bridge(select())
        elif fault == 'edited':
            await queue(owner, child, 'edit', id=row['id'], revision=before['revision'], content='EDITED')
        elif fault == 'cancel':
            await queue(owner, child, 'return_cancel', id=row['id'], revision=before['revision'],
                        token=command['token'])
        elif fault == 'expired':
            now = module.time.monotonic()
            monkeypatch.setattr(module.time, 'monotonic', lambda: now + 31)
        else:
            await owner.control('cancel', child)
        with pytest.raises(ValueError):
            await queue(owner, child, 'return_commit', lease=lease, **command)
        actual = (await queue(owner, child))['queue']['inputs']
        assert len(actual) == 1 and actual[0]['id'] == row['id'] and len(calls) == 1
    finally:
        await owner.aclose()


async def test_cancelled_byte_preparation_creates_no_lease_and_never_removes_original(monkeypatch):
    import xdog.agent.supervision_return as module
    owner, child, _ = await setup()
    started = threading.Event()
    original = module.prepare_recovery
    async def held(row):
        started.set()
        await asyncio.sleep(60)
        return await original(row)
    monkeypatch.setattr(module, 'prepare_recovery', held)
    work = None
    try:
        await owner.control('steer', child, 'STAYS QUEUED', images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        work = asyncio.create_task(queue(owner, child, 'return_prepare', id=before['inputs'][0]['id'],
                                         revision=before['revision']))
        assert await asyncio.to_thread(started.wait, 3)
        work.cancel()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert (await queue(owner, child))['queue'] == before
        assert not owner._children[child].live_returns.leases
    finally:
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.aclose()


async def test_queue_edit_during_return_preparation_refuses_old_copy_without_removal(monkeypatch):
    import xdog.agent.supervision_return as module
    owner, child, _ = await setup()
    entered, release = threading.Event(), threading.Event()
    original = module.prepare_recovery
    async def held(row):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.003)
        return await original(row)
    monkeypatch.setattr(module, 'prepare_recovery', held)
    work = None
    try:
        await owner.control('steer', child, 'ORIGINAL', images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        row = before['inputs'][0]
        work = asyncio.create_task(queue(owner, child, 'return_prepare', id=row['id'], revision=before['revision']))
        assert await asyncio.to_thread(entered.wait, 3)
        await queue(owner, child, 'edit', id=row['id'], revision=before['revision'], content='NEW TEXT')
        release.set()
        with pytest.raises(ValueError, match='changed during'):
            await work
        actual = (await queue(owner, child))['queue']['inputs']
        assert len(actual) == 1 and actual[0]['content'] == 'NEW TEXT'
        assert not owner._children[child].live_returns.leases
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.aclose()


async def test_return_leases_are_bounded_owner_scoped_and_completed_receipt_is_not_mutable():
    owner, child, _ = await setup()
    try:
        await owner.control('steer', child, 'BOUNDED', images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        row = before['inputs'][0]
        leases = [await queue(owner, child, 'return_prepare', id=row['id'], revision=before['revision'])
                  for _ in range(4)]
        with pytest.raises(ValueError, match='capacity'):
            await queue(owner, child, 'return_prepare', id=row['id'], revision=before['revision'])
        command = {'id': row['id'], 'revision': before['revision'], 'token': leases[0]['queue']['return_token'],
                   'receipt': leases[0]['queue']['image_receipt']}
        with pytest.raises(ValueError, match='foreign'):
            await queue(owner, child, 'return_cancel', lease='other-owner', id=row['id'],
                        revision=before['revision'], token=command['token'])
        result = await queue(owner, child, 'return_commit', **command)
        result['queue']['returned']['content'] = 'MUTATED CLIENT COPY'
        retry = await queue(owner, child, 'return_commit', **command)
        assert retry['queue']['returned']['content'] == 'BOUNDED'
        with pytest.raises(ValueError, match='replay owner'):
            await queue(owner, child, 'return_commit', lease='other-owner', **command)
        with pytest.raises(ValueError):
            await queue(owner, child, 'return_commit', **{**command, 'token': leases[1]['queue']['return_token']})
        assert not (await queue(owner, child))['queue']['inputs']
    finally:
        await owner.aclose()


async def test_revoked_host_guard_is_checked_on_actual_removal_owner_loop():
    owner, child, _ = await setup()
    try:
        await owner.control('steer', child, 'DO NOT REMOVE', images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        row = before['inputs'][0]
        prepared = await queue(owner, child, 'return_prepare', id=row['id'], revision=before['revision'])
        command = {'action': 'return_commit', 'id': row['id'], 'revision': before['revision'],
                   'token': prepared['queue']['return_token'], 'receipt': prepared['queue']['image_receipt']}
        with pytest.raises(ValueError, match='revoked before admission'):
            await owner.control('queue', child, json.dumps(command), return_owner='native-owner',
                                return_alive=lambda: False)
        assert (await queue(owner, child))['queue'] == before
    finally:
        await owner.aclose()


async def test_concurrent_preparations_reserve_capacity_before_yield_and_cancel_releases_slot(monkeypatch):
    import xdog.agent.supervision_return as module
    owner, child, calls = await setup()
    entered, release = threading.Event(), threading.Event()
    count = []
    original = module.prepare_recovery
    async def held(row):
        count.append(row.id)
        if len(count) == 4:
            entered.set()
        while not release.is_set():
            await asyncio.sleep(0.003)
        return await original(row)
    monkeypatch.setattr(module, 'prepare_recovery', held)
    works = []
    try:
        await owner.control('steer', child, 'BOUNDED CONCURRENT PHOTO',
                            images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        item = before['inputs'][0]
        fields = {'id': item['id'], 'revision': before['revision']}
        works = [asyncio.create_task(queue(owner, child, 'return_prepare', **fields)) for _ in range(4)]
        assert await asyncio.to_thread(entered.wait, 3)
        assert owner._children[child].live_returns.preparing == 4
        with pytest.raises(ValueError, match='capacity'):
            await queue(owner, child, 'return_prepare', **fields)
        assert len(count) == 4  # Fifth request never starts binary preparation.
        works[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await works[0]
        assert owner._children[child].live_returns.preparing == 3
        works.append(asyncio.create_task(queue(owner, child, 'return_prepare', **fields)))
        async with asyncio.timeout(3):
            while len(count) != 5:
                await asyncio.sleep(0.003)
        assert owner._children[child].live_returns.preparing == 4
        release.set()
        results = await asyncio.gather(*works[1:])
        assert len({result['queue']['return_token'] for result in results}) == 4
        assert owner._children[child].live_returns.preparing == 0
        assert len(owner._children[child].live_returns.leases) == 4
        with pytest.raises(ValueError, match='capacity'):
            await queue(owner, child, 'return_prepare', **fields)
        assert (await queue(owner, child))['queue'] == before and len(calls) == 1
    finally:
        release.set()
        await asyncio.gather(*works, return_exceptions=True)
        await owner.aclose()


async def test_real_cancelled_image_thread_retains_preparation_slot_until_worker_exits(monkeypatch):
    import xdog.agent.supervision_transfer as transfer
    owner, child, calls = await setup()
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = transfer.validate_images
    def held(images):
        entered.set()
        try:
            assert release.wait(5)
            original(images)
        finally:
            exited.set()
    work = None
    try:
        await owner.control('steer', child, 'REAL THREAD COPY', images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        item = before['inputs'][0]
        monkeypatch.setattr(transfer, 'validate_images', held)
        work = asyncio.create_task(queue(owner, child, 'return_prepare', id=item['id'], revision=before['revision']))
        assert await asyncio.to_thread(entered.wait, 3)
        work.cancel()
        await asyncio.sleep(0.03)
        work.cancel()  # Repeated cancellation must not detach the actual decode/hash owner.
        await asyncio.sleep(0.03)
        assert not work.done() and not exited.is_set()
        assert owner._children[child].live_returns.preparing == 1
        assert not owner._children[child].live_returns.leases
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert exited.is_set() and owner._children[child].live_returns.preparing == 0
        assert (await queue(owner, child))['queue'] == before and len(calls) == 1
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.aclose()


async def main_tool_call(owner, child, command, *, hook=None, extras=None, parents=None,
                         profile=None, initial_images=()):
    """Execute through a real parent Agent's schema validation and permission hook."""
    from xdog.ai.types import Model, ToolCall, ToolResultMessage

    class MainStream:
        def __init__(self):
            self.calls = 0
        def resolve_model(self, name):
            return profile or Model(id='offline/main', input=('text', 'image'))
        def __call__(self, *args):
            self.calls += 1
            content = ((ToolCall(id='control', name='agent_control', arguments={
                'action': 'queue', 'id': child, 'text': json.dumps(command), **(extras or {})}),)
                if self.calls == 1 else (TextContent(text='CONTROL SETTLED'),))
            return EventStream.empty(AssistantMessage(content=content))

    parent = Agent(MainStream(), config=AgentConfig(model='offline/main'), tools=owner.tools(),
                   before_tool_call=hook)
    if parents is not None:
        parents.append(parent)
    async for _ in await parent.prompt('Perform the explicitly requested child queue control',
                                       images=list(initial_images)):
        pass
    return next(message for message in parent.messages if isinstance(message, ToolResultMessage))


def tool_metadata(result):
    return json.loads(next(part.text for part in result.content if isinstance(part, TextContent)))


async def prepare_main_return(owner, child):
    await owner.control('follow-up', child, 'MAIN AGENT PHOTO', images=(ImageContent(data=PNG, mime_type='image/png'),))
    before = tool_metadata(await main_tool_call(owner, child, {'action': 'list'}))['queue']
    row = before['inputs'][0]
    prepared = await main_tool_call(owner, child, {'action': 'return_prepare', 'id': row['id'],
                                                  'revision': before['revision']})
    assert not prepared.is_error
    metadata = tool_metadata(prepared)
    command = {'action': 'return_commit', 'id': row['id'], 'revision': before['revision'],
               'token': metadata['queue']['return_token'], 'receipt': metadata['queue']['image_receipt']}
    return before, prepared, command


async def test_main_agent_actual_tool_can_prepare_commit_without_serializing_owner_or_binary_text():
    owner, child, calls = await setup()
    seen = []
    async def policy(ctx, cancel):
        seen.append(ctx.tool_call.name)
    try:
        before, prepared, command = await prepare_main_return(owner, child)
        assert [part for part in prepared.content if isinstance(part, ImageContent)] == [
            ImageContent(data=PNG, mime_type='image/png')]
        assert PNG not in prepared.content[0].text
        assert (await queue(owner, child))['queue'] == before
        nonce = owner._tool_return_owner
        assert nonce not in json.dumps(owner.snapshot()) and nonce not in prepared.content[0].text
        assert all('owner' not in key for key in tool_metadata(prepared))
        # Refreshing the host's tool inventory must not rotate outstanding ownership.
        owner.tools()
        assert owner._tool_return_owner == nonce
        committed = await main_tool_call(owner, child, command, hook=policy)
        assert not committed.is_error and seen == ['agent_control']
        assert tool_metadata(committed)['queue']['returned'] == before['inputs'][0]
        assert not (await queue(owner, child))['queue']['inputs'] and len(calls) == 1
        # No automatic retry is performed. An explicit exact retry is a copied receipt only.
        retry = await main_tool_call(owner, child, command, hook=policy)
        assert tool_metadata(retry) == tool_metadata(committed)
        assert len(calls) == 1 and seen == ['agent_control', 'agent_control']
    finally:
        await owner.aclose()


@pytest.mark.parametrize('fault', ['expired', 'owner', 'receipt', 'revision', 'selected', 'cancel', 'closed'])
async def test_main_agent_tool_refuses_expired_foreign_changed_or_selected_return(fault):
    from dataclasses import replace

    owner, child, calls = await setup()
    try:
        before, _, command = await prepare_main_return(owner, child)
        if fault == 'expired':
            async def expire():
                leases = owner._children[child].live_returns.leases
                leases[command['token']] = replace(leases[command['token']], deadline=0)
            await owner._bridge(expire())
        elif fault == 'owner':
            async def retire():
                owner._tool_return_owner = 'new-supervisor-owned-nonce'
            await owner._bridge(retire())
        elif fault == 'receipt':
            command['receipt'] = {**command['receipt'], 'content': 'WRONG'}
        elif fault == 'revision':
            command['revision'] = '0' * 64
        elif fault == 'selected':
            async def select():
                owner._children[child].agent._dequeue_follow_ups()
            await owner._bridge(select())
        elif fault == 'cancel':
            canceled = await main_tool_call(owner, child, {key: value for key, value in {
                **command, 'action': 'return_cancel'}.items() if key != 'receipt'})
            assert not canceled.is_error
        else:
            await owner.aclose()
        result = await main_tool_call(owner, child, command)
        assert result.is_error
        assert len(calls) == 1
        actual = (owner._children[child].agent.pending_inputs() if fault != 'closed'
                  else owner._children[child].undelivered)
        assert len(actual) == 1 and actual[0].id == before['inputs'][0]['id']
        assert actual[0].images == (ImageContent(data=PNG, mime_type='image/png'),)
    finally:
        await owner.aclose()


async def test_main_agent_return_tool_obeys_host_denial_and_rejects_model_supplied_owner():
    from xdog.agent import BeforeToolCallResult

    owner, child, _ = await setup()
    seen = []
    async def deny(ctx, cancel):
        seen.append(ctx.args)
        return BeforeToolCallResult(block=True, reason='HOST POLICY DENIED')
    try:
        before, _, command = await prepare_main_return(owner, child)
        denied = await main_tool_call(owner, child, command, hook=deny)
        assert denied.is_error and 'HOST POLICY DENIED' in denied.content[0].text
        assert seen == [{'action': 'queue', 'id': child, 'text': json.dumps(command)}]
        forged = await main_tool_call(owner, child, command, extras={'return_owner': owner._tool_return_owner})
        assert forged.is_error
        nested = await main_tool_call(owner, child, {**command, 'return_owner': owner._tool_return_owner})
        assert nested.is_error
        assert (await queue(owner, child))['queue'] == before
    finally:
        await owner.aclose()


async def test_actual_main_agent_abort_during_preparation_settles_bytes_without_removal(monkeypatch):
    import xdog.agent.supervision_transfer as transfer

    owner, child, calls = await setup()
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = transfer.validate_images
    def held(images):
        entered.set()
        try:
            assert release.wait(5)
            original(images)
        finally:
            exited.set()
    work = None
    parents = []
    try:
        await owner.control('steer', child, 'CANCELED TOOL PHOTO',
                            images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        row = before['inputs'][0]
        monkeypatch.setattr(transfer, 'validate_images', held)
        work = asyncio.create_task(main_tool_call(owner, child, {'action': 'return_prepare', 'id': row['id'],
                                                               'revision': before['revision']}, parents=parents))
        assert await asyncio.to_thread(entered.wait, 3)
        parents[0].abort()
        await asyncio.sleep(.03)
        assert not work.done() and not exited.is_set()
        assert owner._children[child].live_returns.preparing == 1
        release.set()
        result = await work
        assert result.is_error and exited.is_set()
        assert not owner._children[child].live_returns.leases
        assert (await queue(owner, child))['queue'] == before and len(calls) == 1
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.aclose()


@pytest.mark.parametrize('fault', ['selected', 'cancel', 'owner', 'write'])
async def test_actual_main_tool_commit_rechecks_after_owned_journal_work(tmp_path, fault):
    from packages.agent.tests.test_child_input_journal import running

    owner, store, child = await running(tmp_path)
    owner.persist_return = store.save_return
    entered, release = threading.Event(), threading.Event()
    work = None
    parents = []
    try:
        before, _, command = await prepare_main_return(owner, child)
        original = owner.persist_inputs
        writes = []
        def held(identifier, rows):
            writes.append(rows)
            original(identifier, rows)
            entered.set()
            assert release.wait(5)
            if fault == 'write':
                raise OSError('OWNED JOURNAL FAILURE')
        owner.persist_inputs = held
        nonce = owner._tool_return_owner
        work = asyncio.create_task(main_tool_call(owner, child, command, parents=parents))
        assert await asyncio.to_thread(entered.wait, 3)
        assert owner._children[child].queue_work
        if fault == 'selected':
            async def select():
                owner._children[child].agent._dequeue_follow_ups()
            await owner._bridge(select())
        elif fault == 'cancel':
            parents[0].abort()
            await asyncio.sleep(.03)
            assert not work.done()  # The actual disk owner must settle before cancellation returns.
        elif fault == 'owner':
            owner.abort()
            assert owner._tool_return_owner != nonce
        release.set()
        result = await work
        assert result.is_error and len(writes) == 1
        if fault == 'owner':
            async with asyncio.timeout(3):
                while owner._children[child].agent is not None:
                    await asyncio.sleep(.003)
            rows = owner._children[child].undelivered
        else:
            rows = owner._children[child].agent.pending_inputs()
        assert len(rows) == 1 and rows[0].id == before['inputs'][0]['id']
        assert rows[0].images == (ImageContent(data=PNG, mime_type='image/png'),)
        backup = owner._children[child].returned_backups
        assert len(backup) == 1 and backup[0].images == rows[0].images
        assert not owner._children[child].queue_work
        # Any uncertain journal outcome retains the conservative exact durable copy.
        assert store.load()[0]['returned_backups'][0]['images'][0]['data'] == PNG
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.aclose()


async def test_main_tool_preparation_capacity_and_terminal_lifetime_do_not_retain_unbounded_bytes():
    owner, child, _ = await setup()
    try:
        await owner.control('steer', child, 'CAPACITY PHOTO', images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        command = {'action': 'return_prepare', 'id': before['inputs'][0]['id'], 'revision': before['revision']}
        for _ in range(4):
            assert not (await main_tool_call(owner, child, command)).is_error
        refused = await main_tool_call(owner, child, command)
        assert refused.is_error and 'capacity' in refused.content[0].text
        assert len(owner._children[child].live_returns.leases) == 4
        assert (await queue(owner, child))['queue'] == before
        nonce = owner._tool_return_owner
        owner.abort()
        assert owner._tool_return_owner != nonce
        async with asyncio.timeout(3):
            while owner._children[child].agent is not None:
                await asyncio.sleep(.003)
        assert not owner._children[child].live_returns.leases
        assert not owner._children[child].live_returns.completed
        assert owner._children[child].undelivered[0].images == (ImageContent(data=PNG, mime_type='image/png'),)
    finally:
        nonce = owner._tool_return_owner
        await owner.aclose()
        assert owner._tool_return_owner != nonce


async def test_main_agent_prepare_is_permissioned_before_copy_or_lease_admission():
    from xdog.agent import BeforeToolCallResult

    owner, child, _ = await setup()
    async def deny(ctx, cancel):
        return BeforeToolCallResult(block=True, reason='PREPARATION NOT APPROVED')
    try:
        await owner.control('steer', child, 'PRIVATE PHOTO', images=(ImageContent(data=PNG, mime_type='image/png'),))
        before = (await queue(owner, child))['queue']
        refused = await main_tool_call(owner, child, {'action': 'return_prepare', 'id': before['inputs'][0]['id'],
                                                    'revision': before['revision']}, hook=deny)
        assert refused.is_error and 'PREPARATION NOT APPROVED' in refused.content[0].text
        assert not any(isinstance(part, ImageContent) for part in refused.content)
        assert not owner._children[child].live_returns.leases
        assert not owner._children[child].live_returns.preparing
        assert (await queue(owner, child))['queue'] == before
    finally:
        await owner.aclose()


@pytest.mark.parametrize('phase', ['prepared', 'preparing', 'durable-backup'])
async def test_main_tool_retained_image_bytes_still_count_after_explicit_queue_removal(tmp_path, monkeypatch, phase):
    import xdog.agent.supervision_images as image_module
    import xdog.agent.supervision_transfer as transfer
    from xdog.agent.supervision_store import ChildStore

    owner, child, _ = await setup()
    body = base64.b64decode(PNG)
    first = ImageContent(data=base64.b64encode(body + b'a').decode(), mime_type='image/png')
    second = ImageContent(data=base64.b64encode(body + b'b').decode(), mime_type='image/png')
    # Each image fits; retaining both distinct immutable byte bodies does not.
    monkeypatch.setattr(image_module, 'MAX_CHILD_IMAGE_BYTES', len(body) + 2)
    entered, release = threading.Event(), threading.Event()
    work = None
    original = transfer.validate_images
    def held(images):
        entered.set()
        assert release.wait(5)
        original(images)
    try:
        if phase == 'durable-backup':
            store = ChildStore(tmp_path / 'records')
            store.begin(owner._children[child].spec)
            owner.persist = store.save
            owner.persist_inputs = store.save_inputs
            owner.persist_return = store.save_return
        await owner.control('steer', child, 'RETAINED PHOTO', images=(first,))
        before = (await queue(owner, child))['queue']
        row = before['inputs'][0]
        command = {'action': 'return_prepare', 'id': row['id'], 'revision': before['revision']}
        if phase == 'preparing':
            monkeypatch.setattr(transfer, 'validate_images', held)
            work = asyncio.create_task(main_tool_call(owner, child, command))
            assert await asyncio.to_thread(entered.wait, 3)
        else:
            prepared = await main_tool_call(owner, child, command)
            assert not prepared.is_error
            metadata = tool_metadata(prepared)
        if phase == 'durable-backup':
            committed = await main_tool_call(owner, child, {
                'action': 'return_commit', 'id': row['id'], 'revision': before['revision'],
                'token': metadata['queue']['return_token'], 'receipt': metadata['queue']['image_receipt']})
            assert not committed.is_error
        else:
            removed = await main_tool_call(owner, child, {'action': 'remove', 'id': row['id'],
                                                          'revision': before['revision']})
            assert not removed.is_error
        with pytest.raises(ValueError, match='retained image budget'):
            await owner.control('steer', child, 'MUST NOT OVERFILL', images=(second,))
        assert not (await queue(owner, child))['queue']['inputs']
        if phase == 'prepared':
            canceled = await main_tool_call(owner, child, {
                'action': 'return_cancel', 'id': row['id'], 'revision': before['revision'],
                'token': metadata['queue']['return_token']})
            assert not canceled.is_error
        elif phase == 'preparing':
            release.set()
            assert (await work).is_error  # Removed input is not revived or copied after the held work.
            assert not owner._children[child].live_returns.retained_images()
        else:
            assert owner._children[child].returned_backups[0].images == (first,)
            assert store.load()[0]['returned_backups'][0]['images'][0]['data'] == first.data
            return  # Durable bytes remain bounded until explicit retained-backup disposal.
        await owner.control('steer', child, 'NOW FITS', images=(second,))
        assert owner._children[child].agent.pending_inputs()[0].images == (second,)
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.aclose()


@pytest.mark.parametrize('limit', ['image-count', 'image-size', 'text-only'])
async def test_main_tool_typed_return_keeps_actual_parent_image_context_gates(limit):
    from xdog.ai.types import Model, VisionLimits

    owner, child, _ = await setup()
    parents = []
    image = ImageContent(data=PNG, mime_type='image/png')
    profile = Model(id='offline/main', input=('text',) if limit=='text-only' else ('text','image'),
                    vision_limits=VisionLimits(max_prompt_images=1,
                                               max_prompt_image_size=1 if limit=='image-size' else 1024,
                                               supported_media_types=('image/png',)))
    try:
        await owner.control('steer', child, 'NO LOSSY FALLBACK', images=(image,))
        before = (await queue(owner, child))['queue']
        prepared = await main_tool_call(owner, child, {
            'action': 'return_prepare', 'id': before['inputs'][0]['id'], 'revision': before['revision']},
            profile=profile, initial_images=(image,) if limit=='image-count' else (), parents=parents)
        # Copy really exists as typed history, but the next adapter request is refused.
        # The runtime does not turn it into successful text-only/model completion.
        assert not prepared.is_error and image in prepared.content
        assert parents[0].state.error
        if limit=='image-count':
            assert 'image-count' in parents[0].state.error
        assert not any(isinstance(part, TextContent) and part.text=='CONTROL SETTLED'
                       for message in parents[0].messages if isinstance(message, AssistantMessage)
                       for part in message.content)
        assert (await queue(owner, child))['queue'] == before
        assert len(owner._children[child].live_returns.leases) == 1
    finally:
        await owner.aclose()
