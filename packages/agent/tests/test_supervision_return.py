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
