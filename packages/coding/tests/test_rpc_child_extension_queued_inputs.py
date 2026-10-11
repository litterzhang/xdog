"""Original child Agent queue uses independent review, original loop and canonical recovery."""
from __future__ import annotations

import asyncio
import threading

import pytest
from xdog.ai.types import AssistantMessage, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_rpc_children import close, control, packet, setup, wait


class QueueModel:
    def __init__(self, *, delivery='steer', image=False, ending='apply'):
        self.calls = []
        self.loop = None
        self.delivery, self.image, self.ending = delivery, image, ending

    def resolve_model(self, name):
        return Model(id=name, context_window=128000, input=('text', 'image'))

    def __call__(self, model, context, options):
        self.loop = asyncio.get_running_loop()
        self.calls.append(context)
        message = (AssistantMessage(content=(ToolCall(id='queue', name=next(tool.name for tool in context.tools
            if tool.name.startswith('ext_queuedhost_request')), arguments={
                'delivery': self.delivery, 'image': self.image, 'ending': self.ending}),))
            if len(self.calls) == 1 else AssistantMessage(content=(TextContent(text='CHILD-DONE'),)))
        return EventStream.empty(message)


async def first_dialog(events):
    await wait(lambda: any(row['type'] == 'child_extension_dialog' for row in events))
    return next(row for row in events if row['type'] == 'child_extension_dialog')


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'error', 'image', 'withdraw'])
async def test_actual_rpc_child_canonical_queue_and_capability_ownership(tmp_path, delivery, ending):
    session, _, nav, sources, children, events = setup(tmp_path, mode='allow-all', extension='queuedhost')
    model = QueueModel(delivery=delivery, image=ending == 'image', ending=ending)
    session.agent.set_stream_fn(model)
    session.agent.queue_input('parent-only', 'PRIVATE PARENT INPUT', 'follow_up')
    children.negotiate({'child_extension_queued_inputs': 1})
    try:
        await control(children, 'start', task='child queue', access='inherit')
        shown = await first_dialog(events)
        child = shown['child_id']
        assert shown['dialog']['operation'] == 'queue_input' and shown['dialog']['images'] == []
        assert shown['queued_input_generation'] == session.children.extensions.queued_input_generation(children.lease)
        agent = session.children.supervisor._children[child].agent
        queue = agent.queue_input
        def owned_queue(*args, **kwargs):
            assert asyncio.get_running_loop() is model.loop
            return queue(*args, **kwargs)
        agent.queue_input = owned_queue
        if ending == 'withdraw':
            children.negotiate({'child_extension_queued_inputs': 0})
        else:
            children.answer_dialog(packet(children, child_id=child, id=shown['dialog']['id'], value=ending != 'deny'))
        await wait(lambda: not session.children.supervisor.active)
        await asyncio.sleep(.02)
        received = [row for row in events if row['type'] == 'child_extension_input_queued']
        consumed = [row for row in events if row['type'] == 'child_extension_input_consumed']
        accepted = ending in {'apply', 'image'}
        assert len(received) == len(consumed) == int(accepted)
        if accepted:
            assert received[0]['id'] == consumed[0]['id'] and received[0]['child_id'] == child
            assert received[0]['delivery'] == delivery
            assert received[0]['image_count'] == int(ending == 'image')
        assert not session.messages and session.agent.pending_inputs()[0].id == 'parent-only'
        assert not any('PRIVATE PARENT INPUT' in str(call.messages) for call in model.calls)
    finally:
        await close(session, nav, sources, children)


async def test_rpc_child_queue_unnegotiated_cannot_borrow_parent_or_generic_dialog_capability(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path, mode='allow-all', extension='queuedhost')
    session.agent.set_stream_fn(QueueModel())
    children.negotiate({'child_dialogs': 1, 'child_forms': 1, 'child_queues': 1})
    async def parent_only(*args):
        pytest.fail('Child borrowed parent queued-input handler')
    session.extensions.queued_input_handler = parent_only
    try:
        await control(children, 'start', task='child queue', access='inherit')
        await wait(lambda: not session.children.supervisor.active)
        assert not any(row['type'] in {'child_extension_dialog', 'child_extension_input_queued'} for row in events)
    finally:
        await close(session, nav, sources, children)


@pytest.mark.parametrize('ending', ['failed-write', 'withdraw'])
async def test_child_awaits_actual_receipt_delivery_and_failure_removes_only_own_input(tmp_path, ending):
    session, _, nav, sources, children, events = setup(tmp_path, mode='allow-all', extension='queuedhost')
    model = QueueModel()
    session.agent.set_stream_fn(model)
    children.negotiate({'child_extension_queued_inputs': 1})
    entered, finish = asyncio.Event(), asyncio.Event()
    async def send(value):
        assert asyncio.get_running_loop() is children.loop
        if value['type'] == 'child_extension_input_queued':
            entered.set()
            await finish.wait()
            if ending == 'failed-write':
                raise OSError('Owned socket write failed')
        events.append(value)
    children.send = send
    try:
        await control(children, 'start', task='child queue', access='inherit')
        shown = await first_dialog(events)
        child = shown['child_id']
        children.answer_dialog(packet(children, child_id=child, id=shown['dialog']['id'], value=True))
        await asyncio.wait_for(entered.wait(), 5)
        assert len(model.calls) == 1
        if ending == 'withdraw':
            children.negotiate({'child_extension_queued_inputs': 0})
        finish.set()
        await wait(lambda: not session.children.supervisor.active)
        assert not any(row['type'] == 'child_extension_input_consumed' for row in events)
        record = await session.children.supervisor.control('inspect', child)
        assert not record.get('pending_inputs') and not record.get('undelivered')
    finally:
        finish.set()
        await close(session, nav, sources, children)


async def test_aborted_child_retains_unconsumed_image_in_canonical_recovery_without_parent_draft(tmp_path):
    from packages.coding.tests.test_rpc_images import PNG
    session, _, nav, sources, children, events = setup(tmp_path, mode='allow-all', extension='queuedhost')
    model = QueueModel(delivery='follow_up', image=True)
    session.agent.set_stream_fn(model)
    children.negotiate({'child_extension_queued_inputs': 1})
    blocked = threading.Event()
    factory = session.children.supervisor.factory
    async def checkpoint_factory(spec, publish):
        agent = await factory(spec, publish)
        async def checkpoint(event):
            if 'REVIEWED-QUEUE' in str(getattr(event, 'message', None)):
                blocked.set()
                await asyncio.Event().wait()
        agent.set_input_checkpoint(checkpoint)  # Install while still idle, before supervisor wrapping.
        return agent
    session.children.supervisor.factory = checkpoint_factory
    try:
        await control(children, 'start', task='child queue', access='inherit')
        shown = await first_dialog(events)
        child = shown['child_id']
        children.answer_dialog(packet(children, child_id=child, id=shown['dialog']['id'], value=True))
        await wait(blocked.is_set)
        await session.children.supervisor.control('cancel', child)
        await wait(lambda: not session.children.supervisor.active)
        record = await session.children.supervisor.control('inspect', child)
        # Both the current record and persisted recovery must retain typed bytes.
        assert PNG in str(record), record
        persisted = session.children.store.load()
        assert PNG in str(persisted), persisted
        assert not session.messages and not session.agent.pending_inputs()
        assert not any(row['type'] == 'child_extension_input_consumed' for row in events)
        assert any(row['type'] == 'child_extension_input_returned' for row in events)
    finally:
        await close(session, nav, sources, children)


async def test_child_live_pending_boolean_runs_on_original_child_loop_never_reads_parent(tmp_path):
    import json

    from xdog.ai.types import StartEvent
    session, _, nav, sources, children, events = setup(tmp_path, extension='queuedrequest')
    entered = threading.Event()
    child_loop = []
    def stream(model, context, options):
        child_loop[:] = [asyncio.get_running_loop()]
        async def generate():
            entered.set()
            yield StartEvent(partial=AssistantMessage())
            await asyncio.Event().wait()
        return EventStream(generate())
    session.agent.set_stream_fn(stream)
    session.agent.queue_input('parent-only', 'PRIVATE PARENT INPUT', 'steer')
    session.agent.has_queued_messages = lambda: pytest.fail('Child inspected parent queue')
    try:
        await control(children, 'start', task='held child')
        await wait(entered.is_set)
        child = next(iter(session.children.extensions.owners))
        runtime = session.children.extensions.owners[child]
        agent = session.children.supervisor._children[child].agent
        original = agent.has_queued_messages
        def read():
            assert asyncio.get_running_loop() is child_loop[0]
            return original()
        agent.has_queued_messages = read
        assert json.loads(await runtime.command('ext-queuedrequest-pending', '')) == [False, False]
        await session.children.supervisor.control('follow-up', child, 'CHILD OWN PENDING')
        assert json.loads(await runtime.command('ext-queuedrequest-pending', '')) == [True, True]
        assert not session.messages and session.agent.pending_inputs()[0].id == 'parent-only'
    finally:
        await close(session, nav, sources, children)
