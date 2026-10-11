"""Durable undelivered child input copies; no automatic child restart or canonical replay."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSupervisor
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream


async def owned(tmp_path):
    entered = threading.Event()
    calls = []
    def factory(spec, publish):
        def stream(*args):
            calls.append(args)
            async def generate():
                entered.set()
                yield StartEvent(partial=AssistantMessage(content=(TextContent(text='HELD'),)))
                await asyncio.sleep(60)
            return EventStream.from_async_generator(generate())
        return Agent(stream, config=AgentConfig(model='offline/model'))
    store = ChildStore.scoped(tmp_path, 'session', 'head')
    owner = ChildSupervisor(factory, store.save, begin=store.begin, records=store.load())
    child = await owner.start('ORIGINAL TASK')
    assert await asyncio.to_thread(entered.wait, 3)
    return owner, store, child, calls


async def queue(owner, child, **value):
    return (await owner.control('queue', child, json.dumps(value)))['queue']


async def test_cancel_retains_unadmitted_inputs_reload_copy_and_discard_are_explicit_and_durable(tmp_path):
    owner, store, child, calls = await owned(tmp_path)
    try:
        await owner.control('steer', child, '/quit retained as literal text')
        await owner.control('follow-up', child, 'SECOND DRAFT')
        await owner.control('cancel', child)
        actual = await queue(owner, child, action='list')
        assert actual['stopped'] and [row['content'] for row in actual['inputs']] == [
            '/quit retained as literal text', 'SECOND DRAFT']
        assert len(calls) == 1 and owner._children[child].agent is None
        saved = store.load()[0]
        assert len(saved['undelivered_inputs']) == 2
        assert 'SECOND DRAFT' not in str(saved['messages'])
    finally:
        await owner.aclose()
    def must_not_start(*args):
        raise AssertionError('Restoring records or copying input must not create an agent/model call')
    restored = ChildSupervisor(must_not_start, store.save, records=store.load())
    try:
        captured = await queue(restored, child, action='list')
        first = captured['inputs'][0]
        copied = await queue(restored, child, action='recover', id=first['id'], revision=captured['revision'])
        assert copied['recovered'] == first and copied['inputs'] == captured['inputs']
        assert len(store.load()[0]['undelivered_inputs']) == 2  # Lost receipt cannot erase durable data.
        removed = await queue(restored, child, action='remove', id=first['id'], revision=captured['revision'])
        assert len(removed['inputs']) == 1 and len(store.load()[0]['undelivered_inputs']) == 1
        with pytest.raises(ValueError, match='changed'):
            await queue(restored, child, action='remove', id=first['id'], revision=captured['revision'])
    finally:
        await restored.aclose()


async def test_failed_discard_save_does_not_publish_removed_input_or_corrupt_durable_owner(tmp_path, monkeypatch):
    owner, store, child, _ = await owned(tmp_path)
    try:
        await owner.control('steer', child, 'KEEP IF SAVE FAILS')
        await owner.control('cancel', child)
        before = store._path(child).read_bytes()
        captured = await queue(owner, child, action='list')
        item = captured['inputs'][0]
        def fail(record):
            raise OSError('Owned discard save failed')
        monkeypatch.setattr(owner, 'persist', fail)
        with pytest.raises(OSError):
            await queue(owner, child, action='remove', id=item['id'], revision=captured['revision'])
        assert store._path(child).read_bytes() == before
        assert (await queue(owner, child, action='list'))['inputs'] == captured['inputs']
        assert not owner.active
    finally:
        await owner.aclose()


async def test_recovery_write_cancel_settles_actual_commit_before_releasing_metadata_owner(tmp_path, monkeypatch):
    owner, store, child, _ = await owned(tmp_path)
    entered, release = threading.Event(), threading.Event()
    try:
        await owner.control('steer', child, 'DISCARD AFTER REVIEW')
        await owner.control('cancel', child)
        captured = await queue(owner, child, action='list')
        original = owner.persist
        def held(record):
            original(record)
            entered.set()
            assert release.wait(4)
        monkeypatch.setattr(owner, 'persist', held)
        work = asyncio.create_task(queue(owner, child, action='remove', id=captured['inputs'][0]['id'],
                                         revision=captured['revision']))
        assert await asyncio.to_thread(entered.wait, 3)
        work.cancel()
        await asyncio.sleep(0.02)
        assert not work.done() and owner.active
        with pytest.raises(RuntimeError, match='recovery storage'):
            await owner.control('close', child)
        release.set()
        await asyncio.gather(work, return_exceptions=True)
        assert not owner.active and not (await queue(owner, child, action='list'))['inputs']
        assert not store.load()[0].get('undelivered_inputs')
    finally:
        release.set()
        await owner.aclose()


@pytest.mark.parametrize('corrupt', ['duplicate', 'selected', 'image', 'quota'])
async def test_invalid_saved_recovery_is_refused_not_dropped_or_relaunched(tmp_path, corrupt):
    owner, store, child, _ = await owned(tmp_path)
    await owner.control('steer', child, 'VALID')
    await owner.control('cancel', child)
    await owner.aclose()
    record = json.loads(store._path(child).read_text())
    row = record['undelivered_inputs'][0]
    if corrupt == 'duplicate':
        record['undelivered_inputs'].append(row)
    elif corrupt == 'selected':
        row['selected'] = True
    elif corrupt == 'image':
        row['images'] = [{'data': 'not base64!', 'mime_type': 'image/png'}]
    else:
        row['content'] = 'x' * 16001
    store._path(child).write_text(json.dumps(record))
    with pytest.raises((ValueError, TypeError)):
        store.load()


async def test_actual_consumed_followup_is_not_preserved_as_an_unadmitted_recovery_item(tmp_path):
    from xdog.ai.types import DoneEvent

    entered, release = threading.Event(), threading.Event()
    calls = []
    def factory(spec, publish):
        def stream(model, context, options):
            calls.append(context)
            async def generate():
                if len(calls) == 1:
                    entered.set()
                    while not release.is_set():
                        await asyncio.sleep(0.005)
                message = AssistantMessage(content=(TextContent(text='DONE'),))
                output.set_result(message)
                yield DoneEvent(message=message)
            output = EventStream.from_async_generator(generate())
            return output
        return Agent(stream, config=AgentConfig(model='offline/model'))
    store = ChildStore.scoped(tmp_path, 'session', 'head')
    owner = ChildSupervisor(factory, store.save, begin=store.begin)
    try:
        child = await owner.start('original')
        assert await asyncio.to_thread(entered.wait, 3)
        await owner.control('follow-up', child, 'ACTUALLY ADMITTED FOLLOWUP')
        release.set()
        await owner.control('wait', child)
        record = store.load()[0]
        assert 'ACTUALLY ADMITTED FOLLOWUP' in str(record['messages'])
        assert not record.get('undelivered_inputs') and not (await queue(owner, child, action='list'))['inputs']
        assert len(calls) == 2
    finally:
        release.set()
        await owner.aclose()


async def test_supervisor_shutdown_waits_for_owned_recovery_storage_before_joining_thread(tmp_path, monkeypatch):
    owner, store, child, _ = await owned(tmp_path)
    entered, release = threading.Event(), threading.Event()
    try:
        await owner.control('steer', child, 'REVIEWED DISCARD')
        await owner.control('cancel', child)
        captured = await queue(owner, child, action='list')
        original = owner.persist
        def held(record):
            entered.set()
            assert release.wait(4)
            original(record)
        monkeypatch.setattr(owner, 'persist', held)
        work = asyncio.create_task(queue(owner, child, action='remove', id=captured['inputs'][0]['id'],
                                         revision=captured['revision']))
        assert await asyncio.to_thread(entered.wait, 3)
        closing = asyncio.create_task(owner.aclose())
        await asyncio.sleep(0.03)
        assert not closing.done() and not work.done() and owner._thread.is_alive()
        release.set()
        await work
        await closing
        assert not owner._thread.is_alive() and not store.load()[0].get('undelivered_inputs')
    finally:
        release.set()
        await owner.aclose()


async def test_stopped_image_input_is_persisted_typed_but_cannot_be_copied_lossily_to_text_draft(tmp_path):
    from xdog.ai.types import ImageContent

    owner, store, child, _ = await owned(tmp_path)
    image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                               'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
    try:
        async def enqueue():
            owner._children[child].agent.queue_input('image-draft', 'KEEP IMAGE', 'follow_up', (image,))
        await owner._bridge(enqueue())
        await owner.control('cancel', child)
        captured = await queue(owner, child, action='list')
        assert captured['inputs'][0]['image_count'] == 1 and image.data not in str(captured)
        with pytest.raises(ValueError, match='lossy'):
            await queue(owner, child, action='recover', id='image-draft', revision=captured['revision'])
        loaded = store.load()[0]
        assert loaded['undelivered_inputs'][0]['images'] == [{'data': image.data, 'mime_type': image.mime_type}]
        from xdog.agent.supervision_queue import recovery_from_dicts
        assert recovery_from_dicts(loaded['undelivered_inputs'])[0].images == (image,)
    finally:
        await owner.aclose()


async def test_stopped_image_copy_is_explicit_revisioned_non_destructive_and_never_starts_an_agent(tmp_path):
    from xdog.ai.types import ImageContent
    owner, store, child, calls = await owned(tmp_path)
    image = ImageContent(data=('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE'
                               'QVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='), mime_type='image/png')
    try:
        await owner.control('follow-up', child, 'RECOVER-PHOTO', images=(image,))
        await owner.control('cancel', child)
        before = store.load()[0]
        captured = await queue(owner, child, action='list')
        request = {'action': 'recover_images', 'id': captured['inputs'][0]['id'], 'revision': captured['revision']}
        copied = await owner.control('queue', child, json.dumps(request))
        assert copied['image_recovery']['images'] == [{'data': image.data, 'mime_type': image.mime_type}]
        assert copied['queue']['recovered'] == captured['inputs'][0]
        assert copied['queue']['image_receipt'] == copied['image_recovery']['receipt']
        assert store.load()[0] == before and len(calls) == 1 and not owner.active
        with pytest.raises(ValueError, match='changed'):
            await owner.control('queue', child, json.dumps({**request, 'revision': '0' * 64}))
        assert store.load()[0] == before
    finally:
        await owner.aclose()


@pytest.mark.parametrize('action', ['cancel', 'close'])
async def test_live_child_cancel_and_close_still_settle_writer_instead_of_using_stopped_recovery_guard(
    tmp_path, monkeypatch, action,
):
    owner, store, child, calls = await owned(tmp_path)
    entered, release = threading.Event(), threading.Event()
    captured = owner._children[child]
    writing = closing = None
    def held(identifier, rows):
        entered.set()
        assert release.wait(4)
        store.save_inputs(identifier, rows)
    monkeypatch.setattr(owner, 'persist_inputs', held)
    try:
        writing = asyncio.create_task(owner.control('steer', child, 'NOT ADMITTED DURING STOP'))
        assert await asyncio.to_thread(entered.wait, 3)
        assert captured.agent is not None and captured.queue_work
        closing = asyncio.create_task(owner.control(action, child))
        await asyncio.sleep(.02)
        assert not closing.done() and captured.queue_work  # Stop accepted, actual writer still owns cleanup.
        release.set()
        outcomes = await asyncio.gather(writing, closing, return_exceptions=True)
        assert isinstance(outcomes[0], ValueError)  # Source stopped before post-journal live admission.
        assert not isinstance(outcomes[1], BaseException)
        assert not captured.queue_work and not owner.active and len(calls) == 1
        assert not store.load()[0].get('undelivered_inputs')
    finally:
        release.set()
        await asyncio.gather(*(task for task in (writing, closing) if task is not None), return_exceptions=True)
        await owner.aclose()
