"""Child queue leases use the real owning Agent queue/checkpoint, not mirrored UI records."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.supervision import ChildSupervisor
from xdog.agent.supervision_queue import request
from xdog.ai.types import AssistantMessage, ImageContent, StartEvent, TextContent
from xdog.ai.utils.event_stream import EventStream


async def running():
    entered = threading.Event()
    def stream(*args):
        async def generate():
            entered.set()
            yield StartEvent(partial=AssistantMessage(content=(TextContent(text='HELD'),)))
            await asyncio.sleep(60)
        return EventStream.from_async_generator(generate())
    owner = ChildSupervisor(lambda spec, publish: Agent(stream, config=AgentConfig(model='offline/model')))
    child = await owner.start('task')
    assert await asyncio.to_thread(entered.wait, 3)
    return owner, child


async def queue(owner, child, **value):
    return (await owner.control('queue', child, json.dumps(value)))['queue']


async def test_actual_queue_edit_reorder_return_and_stale_snapshot_refuse_without_model_admission():
    owner, child = await running()
    try:
        for action, text in [('steer', 'first'), ('steer', 'second'), ('follow-up', 'later')]:
            await owner.control(action, child, text)
        original = await queue(owner, child, action='list')
        first, second, later = original['inputs']
        edited = await queue(owner, child, action='edit', id=first['id'], content='/quit is literal child input',
                             revision=original['revision'])
        assert edited['inputs'][0]['content'] == '/quit is literal child input'
        with pytest.raises(ValueError, match='changed'):
            await queue(owner, child, action='remove', id=second['id'], revision=original['revision'])
        moved = await queue(owner, child, action='move', id=second['id'], direction=-1, revision=edited['revision'])
        assert [item['id'] for item in moved['inputs']] == [second['id'], first['id'], later['id']]
        with pytest.raises(ValueError, match='delivery lanes'):
            await queue(owner, child, action='move', id=later['id'], direction=-1, revision=moved['revision'])
        returned = await queue(owner, child, action='return', id=first['id'], revision=moved['revision'])
        assert returned['returned']['content'] == '/quit is literal child input'
        assert all(item['id'] != first['id'] for item in returned['inputs'])
        agent = owner._children[child].agent
        assert len(agent.messages) == 1 and agent.messages[0].content[0].text == 'task'
    finally:
        await owner.aclose()


@pytest.mark.parametrize('action', ['edit', 'remove', 'move', 'return'])
async def test_checkpoint_selected_inputs_refuse_change_even_with_fresh_queue_lease(action):
    owner, child = await running()
    try:
        await owner.control('steer', child, 'CHECKPOINT-SELECTED')
        async def select():
            owner._children[child].agent._dequeue_steering()
        await owner._bridge(select())
        captured = await queue(owner, child, action='list')
        item = captured['inputs'][0]
        assert item['selected']
        fields = {'content': 'replacement'} if action == 'edit' else {'direction': 1} if action == 'move' else {}
        with pytest.raises(ValueError, match='selected'):
            await queue(owner, child, action=action, id=item['id'], revision=captured['revision'], **fields)
    finally:
        await owner.aclose()


async def test_queue_edit_preserves_image_identity_and_return_refuses_lossy_child_draft():
    owner, child = await running()
    try:
        image = ImageContent(data='private-image', mime_type='image/png')
        async def inject():
            owner._children[child].agent.queue_input('image', 'image draft', 'steer', (image,))
        await owner._bridge(inject())
        captured = await queue(owner, child, action='list')
        assert 'private-image' not in str(captured) and captured['inputs'][0]['image_count'] == 1
        changed = await queue(owner, child, action='edit', id='image', content='kept image',
                              revision=captured['revision'])
        assert owner._children[child].agent.pending_inputs()[0].images == (image,)
        with pytest.raises(ValueError, match='lossily'):
            await queue(owner, child, action='return', id='image', revision=changed['revision'])
    finally:
        await owner.aclose()


@pytest.mark.parametrize('value', [{'action': []}, {'action': 'edit'}, {'action': 'list', 'content': 'extra'},
    {'action': 'move', 'id': 'input', 'revision': 'a' * 64, 'direction': True},
    {'action': 'edit', 'id': 'input', 'revision': 'a' * 64, 'content': 'x' * 16001}])
def test_malformed_child_queue_commands_fail_before_mutation(value):
    with pytest.raises(ValueError):
        request(value)
