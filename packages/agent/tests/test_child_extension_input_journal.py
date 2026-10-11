"""Extension identified inputs reuse canonical child journal/cancellation/restart recovery."""
from __future__ import annotations

import asyncio
import json
import select
import subprocess
import sys
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSupervisor
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage, ImageContent
from xdog.ai.utils.event_stream import EventStream

from packages.agent.tests.test_child_input_journal import PNG, running

KEY = 'extq-' + 'a' * 32
IMAGE = ImageContent(data=PNG, mime_type='image/png')


async def admit(owner, child, *, key=KEY, text='EXTENSION QUEUED IMAGE', alive=lambda: True):
    return await owner._admit_identified_input(child, owner._children[child].agent, key,
                                               text, 'follow_up', (IMAGE,), alive)


async def test_old_control_and_extension_share_write_before_enqueue_and_original_loop(tmp_path):
    owner, store, child = await running(tmp_path)
    agent = owner._children[child].agent
    original = agent.queue_input
    observed = []
    def queued(identifier, content, delivery, images=()):
        assert asyncio.get_running_loop() is owner._loop
        saved = store._path(child, '.inputs').read_text()
        assert identifier in saved and content in saved
        observed.append((content, delivery, images))
        return original(identifier, content, delivery, images)
    agent.queue_input = queued
    try:
        await owner.control('follow-up', child, 'ORDINARY', images=(IMAGE,))
        value = await admit(owner, child)
        assert value.id == KEY
        assert observed == [('ORDINARY', 'follow_up', (IMAGE,)), ('EXTENSION QUEUED IMAGE', 'follow_up', (IMAGE,))]
        with pytest.raises(TypeError):
            await owner.control('steer', child, 'no external ID authority', input_id=KEY)
        assert not owner._children[child].queue_work
    finally:
        await owner.aclose()


@pytest.mark.parametrize('change', ['wrong-agent', 'invalid-id', 'revoked', 'bytes', 'duplicate'])
async def test_identified_admission_refuses_forgery_limits_reuse_and_revocation_before_journal(tmp_path, change):
    owner, store, child = await running(tmp_path)
    try:
        await admit(owner, child)
        before = store._path(child, '.inputs').read_bytes()
        target = owner._children[child].agent
        if change == 'wrong-agent':
            target = Agent(lambda *args: EventStream.empty(AssistantMessage()), config=AgentConfig(model='fake'))
        with pytest.raises(ValueError):
            await owner._admit_identified_input(child, target,
                KEY if change == 'duplicate' else 'client-id' if change == 'invalid-id' else 'extq-' + 'b' * 32,
                'x' * 16001 if change == 'bytes' else 'OTHER', 'steer', (), lambda: change != 'revoked')
        assert store._path(child, '.inputs').read_bytes() == before
    finally:
        await owner.aclose()


@pytest.mark.parametrize('stage', ['before-write', 'after-write'])
async def test_identified_writer_failure_never_enqueues_or_acknowledges_and_recovery_is_uncertain(tmp_path, stage):
    owner, store, child = await running(tmp_path)
    original = owner.persist_inputs
    def failed(*args):
        if stage == 'after-write':
            original(*args)
        raise OSError('Owned identified journal failure')
    owner.persist_inputs = failed
    try:
        with pytest.raises(OSError, match='identified journal'):
            await admit(owner, child)
        assert not owner._children[child].agent.pending_inputs()
        assert not owner._children[child].queue_work
        if stage == 'after-write':
            assert KEY in str(store.load()) and PNG in str(store.load())
            assert store.load()[0]['status'] == 'interrupted'
    finally:
        await owner.aclose()


@pytest.mark.parametrize('ending', ['cancel', 'revoke'])
async def test_identified_writer_settles_on_cancel_or_owner_revocation_without_live_admission(tmp_path, ending):
    owner, store, child = await running(tmp_path)
    entered, finish = threading.Event(), threading.Event()
    live = [True]
    original = owner.persist_inputs
    def held(*args):
        original(*args)
        entered.set()
        assert finish.wait(5)
    owner.persist_inputs = held
    task = asyncio.create_task(admit(owner, child, alive=lambda: live[0]))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        if ending == 'cancel':
            task.cancel()
        else:
            live[0] = False
        await asyncio.sleep(.02)
        assert not task.done() and owner._children[child].queue_work
        with pytest.raises(RuntimeError, match='storage still owns'):
            await owner.control('steer', child, 'BLOCKED CONCURRENT WRITER')
        finish.set()
        with pytest.raises(asyncio.CancelledError if ending == 'cancel' else ValueError):
            await task
        assert not owner._children[child].agent.pending_inputs()
        assert not owner._children[child].queue_work
        await owner.control('cancel', child)
        assert not store._path(child, '.inputs').exists()
    finally:
        finish.set()
        await asyncio.gather(task, return_exceptions=True)
        await owner.aclose()


async def test_receipt_failure_discard_is_journaled_and_cannot_remove_manual_or_selected_input(tmp_path):
    owner, store, child = await running(tmp_path)
    try:
        await owner.control('steer', child, 'MANUAL INPUT')
        await admit(owner, child)
        agent = owner._children[child].agent
        assert await owner._discard_identified_input(child, agent, KEY)
        assert [row.content for row in agent.pending_inputs()] == ['MANUAL INPUT']
        assert KEY not in store._path(child, '.inputs').read_text()
        assert not await owner._discard_identified_input(child, agent, agent.pending_inputs()[0].id)
        await admit(owner, child)
        async def select_input():
            agent._dequeue_follow_ups()
        await owner._bridge(select_input())
        assert not await owner._discard_identified_input(child, agent, KEY)
    finally:
        await owner.aclose()


@pytest.mark.skipif(sys.platform != 'linux', reason='Owned current-Linux SIGKILL journal verification')
def test_owned_sigkill_after_identified_ack_restores_image_without_any_automatic_child_restart(tmp_path):
    script = tmp_path / 'identified_owner.py'
    script.write_text('''import asyncio,json,sys
from pathlib import Path
from xdog.agent import Agent,AgentConfig
from xdog.agent.supervision import ChildSupervisor
from xdog.agent.supervision_store import ChildStore
from xdog.ai.types import AssistantMessage,ImageContent,StartEvent
from xdog.ai.utils.event_stream import EventStream
async def main():
    store=ChildStore(Path(sys.argv[1]))
    def factory(spec,publish):
        def stream(*args):
            async def events():
                yield StartEvent(partial=AssistantMessage())
                await asyncio.sleep(60)
            return EventStream.from_async_generator(events())
        return Agent(stream,config=AgentConfig(model='offline/model'))
    owner=ChildSupervisor(factory,store.save,begin=store.begin,persist_inputs=store.save_inputs)
    child=await owner.start('INITIAL')
    while not owner._children[child].agent or not owner._children[child].agent.is_streaming:
        await asyncio.sleep(.003)
    await owner._admit_identified_input(child,owner._children[child].agent,'extq-'+'a'*32,
        'IDENTIFIED IMAGE ACK','follow_up',(ImageContent(data=sys.argv[2],mime_type='image/png'),),lambda:True)
    print(json.dumps({'id':child,'ack':True}),flush=True)
    await asyncio.sleep(60)
asyncio.run(main())
''')
    root = tmp_path / 'records'
    process = subprocess.Popen([sys.executable, '-I', str(script), str(root), PNG],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        receipt = json.loads(process.stdout.readline())
        assert receipt['ack'] is True
        process.kill()  # This exact owned test process only; crash after durable admission receipt.
        process.wait(timeout=5)
        records = ChildStore(root).load()
        assert records[0]['status'] == 'interrupted' and KEY in str(records) and PNG in str(records)
        calls = []
        restored = ChildSupervisor(lambda *args: calls.append(args), records=records)
        try:
            assert not restored.active and not calls
            assert restored.snapshot()['children'][0]['id'] == receipt['id']
        finally:
            restored.close()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
