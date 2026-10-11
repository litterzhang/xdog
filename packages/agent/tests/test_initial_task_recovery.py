"""Retained setup-failed/uncertain initial inputs are recovered without executing or rewriting them."""
from __future__ import annotations

import json

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSupervisor
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage, ImageContent, TextContent, UserMessage

PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j5ioAAAAASUVORK5CYII='


@pytest.mark.parametrize('images', [(), (ImageContent(data=PNG, mime_type='image/png'),)])
async def test_factory_failed_initial_task_is_explicit_non_destructive_and_failed_discard_keeps_owner(tmp_path, images):
    store = ChildStore(tmp_path / 'records')
    calls = []
    def fail(spec, publish):
        calls.append(spec)
        raise ValueError('Owned setup failed before agent creation')
    owner = ChildSupervisor(fail, store.save, begin=store.begin)
    try:
        child = await owner.start('RETAIN INITIAL TASK', images=images)
        record = await owner.control('wait', child)
        assert record['status'] == 'failed' and record['initial_state'] == 'pending' and not record['messages']
        before = store.load()[0]
        snapshot = (await owner.control('queue', child, '{"action":"list"}'))['queue']
        row = snapshot['inputs'][0]
        assert row['source'] == 'initial-task' and row['uncertain'] is False
        request = {'action': 'recover_task', 'id': row['id'], 'revision': snapshot['revision']}
        copied = await owner.control('queue', child, json.dumps(request))
        assert copied['queue']['recovered'] == row and store.load()[0] == before and len(calls) == 1
        assert ('image_recovery' in copied) is bool(images)
        original = owner.persist
        def error(record):
            raise OSError('Owned discard failure')
        owner.persist = error
        with pytest.raises(OSError):
            await owner.control('queue', child, json.dumps({**request, 'action': 'remove_task'}))
        assert store.load()[0] == before and owner.recovery_current(child, request['revision'])
        owner.persist = original
        removed = await owner.control('queue', child, json.dumps({**request, 'action': 'remove_task'}))
        assert not removed['queue']['inputs'] and store.load()[0]['initial_state'] == 'discarded'
        assert not store.load()[0].get('task_images') and len(calls) == 1
    finally:
        await owner.aclose()


async def test_legacy_interrupted_initial_image_copy_is_uncertain_and_reload_never_constructs_agent(tmp_path):
    store = ChildStore(tmp_path / 'records')
    store.save({'id': '0123456789ab', 'task': 'UNCERTAIN ORIGINAL', 'access': 'read-only', 'status': 'interrupted',
                'messages': [], 'task_images': [{'data': PNG, 'mime_type': 'image/png'}]})
    owner = ChildSupervisor(lambda *args: pytest.fail('No implicit restart'), store.save, records=store.load())
    try:
        before = store.load()[0]
        snapshot = (await owner.control('queue', '0123456789ab', '{"action":"list"}'))['queue']
        row = snapshot['inputs'][0]
        assert row['source'] == 'initial-task' and row['uncertain'] is True
        copy = await owner.control('queue', '0123456789ab', json.dumps({
            'action': 'recover_task', 'id': row['id'], 'revision': snapshot['revision']}))
        assert copy['image_recovery']['images'] == before['task_images'] and store.load()[0] == before
    finally:
        await owner.aclose()


async def test_admitted_prompt_failure_does_not_reclassify_inherited_or_actual_input_as_unadmitted(tmp_path):
    def stream(*args):
        raise ValueError('Provider failed after actual prompt admission')
    def factory(spec, publish):
        agent = Agent(stream, config=AgentConfig(model='offline/model'))
        agent.replace_messages([UserMessage(content='OLD PARENT'),
                                AssistantMessage(content=(TextContent(text='OLD ANSWER'),))])
        return agent
    owner = ChildSupervisor(factory)
    try:
        child = await owner.start('INITIAL TASK')
        record = await owner.control('wait', child)
        assert record['status'] == 'failed' and record['initial_state'] == 'admitted'
        assert not (await owner.control('queue', child, '{"action":"list"}'))['queue']['inputs']
    finally:
        await owner.aclose()


async def test_initial_task_discard_cancellation_settles_commit_before_releasing_owner(tmp_path):
    import asyncio
    import threading
    store = ChildStore(tmp_path / 'records')
    def fail(*args):
        raise ValueError('setup failed')
    owner = ChildSupervisor(fail, store.save, begin=store.begin)
    entered, release = threading.Event(), threading.Event()
    work = None
    try:
        child = await owner.start('INITIAL DISCARD', images=(ImageContent(data=PNG, mime_type='image/png'),))
        await owner.control('wait', child)
        snapshot = (await owner.control('queue', child, '{"action":"list"}'))['queue']
        original = owner.persist
        def held(record):
            original(record)
            entered.set()
            assert release.wait(4)
        owner.persist = held
        work = asyncio.create_task(owner.control('queue', child, json.dumps({
            'action': 'remove_task', 'id': snapshot['inputs'][0]['id'], 'revision': snapshot['revision']})))
        assert await asyncio.to_thread(entered.wait, 3)
        work.cancel()
        await asyncio.sleep(0.02)
        assert not work.done() and owner.active
        with pytest.raises(RuntimeError, match='recovery storage'):
            await owner.control('queue', child, '{"action":"list"}')
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await work
        assert store.load()[0]['initial_state'] == 'discarded' and not owner.active
        assert not (await owner.control('queue', child, '{"action":"list"}'))['queue']['inputs']
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await owner.aclose()
