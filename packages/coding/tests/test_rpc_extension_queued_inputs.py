"""A real RPC-owned canonical turn can review tool-origin steer/follow-up inputs."""
from __future__ import annotations

import asyncio

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.rpc_mode import _handle_prompt

from packages.coding.tests.test_rpc_compaction import wait
from packages.coding.tests.test_rpc_extensions import close, session
from packages.coding.tests.test_rpc_images import PNG


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'error', 'image', 'old-client', 'withdraw'])
async def test_real_rpc_tool_queue_proposal_requires_same_active_owner_review_and_callback_success(
    tmp_path, delivery, ending,
):
    owner, _ = session(tmp_path, extra=('queuedhost',))
    owner.permissions = PermissionManager('allow-all')
    owner.model_info = Model(id='fixture', input=('text', 'image'))
    calls, events = [], []
    def stream(model, context, options):
        calls.append(context)
        result = (AssistantMessage(content=(ToolCall(id='request', name=next(tool.name for tool in context.tools
                if tool.name.startswith('ext_queuedhost_request')),
            arguments={'delivery': delivery, 'ending': ending, 'image': ending == 'image'}),))
            if len(calls) == 1 else AssistantMessage(content=(TextContent(text='QUEUE-REPLY'),)))
        return EventStream.empty(result)
    owner.agent.set_stream_fn(stream)
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav)
    original_queue = owner.agent.queue_input
    def owned_queue(*args, **kwargs):
        assert asyncio.get_running_loop() is controller.loop
        return original_queue(*args, **kwargs)
    owner.agent.queue_input = owned_queue
    controller.negotiate({'extension_dialogs': 1, 'extension_editor': 1})
    controller.negotiate({'extension_queued_inputs': int(ending != 'old-client')})
    assert controller.dialogs_enabled and controller.editor_enabled  # Independent negotiation.
    async def run():
        async with controller.bind('original-prompt'):
            await _handle_prompt(owner, 'Original model turn', events.append)
    task = asyncio.create_task(run())
    try:
        if ending != 'old-client':
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            event = next(row for row in events if row['type'] == 'extension_dialog')
            assert event['dialog']['operation'] == 'queue_input' and event['dialog']['images'] == []
            assert PNG not in str(event) and len(calls) == 1
            answer = {'id': event['dialog']['id'], 'request_id': event['request_id'],
                'session_id': event['session_id'], 'scope': event['scope'], 'value': ending != 'deny'}
            with pytest.raises(ValueError):
                controller.answer({**answer, 'scope': 'foreign'})
            controller.answer(answer)
            if ending == 'withdraw':
                controller.negotiate({'extension_queued_inputs': 0})
                controller.negotiate({'extension_queued_inputs': 1})
        async with asyncio.timeout(10):
            await task
        queued = [message for message in owner.messages if message.role == 'user'
                  and 'REVIEWED-QUEUE' in str(message.content)]
        if ending in {'apply', 'image'}:
            assert len(queued) == 1
            parts = queued[0].content
            text = parts if isinstance(parts, str) else parts[0].text
            assert text == '[User-approved message requested by extension queuedhost]\n\nREVIEWED-QUEUE /quit'
            assert sum(isinstance(part, ImageContent) for part in parts) == (1 if ending == 'image' else 0)
            receipt = next(row for row in events if row['type'] == 'extension_input_queued')
            consumed = next(row for row in events if row['type'] == 'extension_input_consumed')
            assert receipt['id'] == consumed['id'] and events.index(receipt) < events.index(consumed)
        else:
            assert not queued and not any(row['type'] == 'extension_input_queued' for row in events)
        assert owner.permissions.mode == 'allow-all' and not owner.agent.pending_inputs()
        assert not controller.queued_inputs._owned
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await controller.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('ending', ['cancel', 'delivery-fail'])
async def test_rpc_owned_queue_returns_unconsumed_images_on_cancel_and_failed_receipt_never_replays(tmp_path, ending):
    from xdog.ai.types import StartEvent
    owner, _ = session(tmp_path, extra=('queuedhost',))
    owner.permissions = PermissionManager('allow-all')
    owner.model_info = Model(id='fixture', input=('text', 'image'))
    events, calls = [], []
    entered, stopped = asyncio.Event(), asyncio.Event()
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='request',
                name=next(tool.name for tool in context.tools if tool.name.startswith('ext_queuedhost_request')),
                arguments={'delivery': 'follow_up', 'image': True}),)))
        if ending == 'delivery-fail':
            return EventStream.empty(AssistantMessage(content=(TextContent(text='DONE'),)))
        async def hold():
            try:
                entered.set()
                yield StartEvent(partial=AssistantMessage())
                await asyncio.Event().wait()
            finally:
                stopped.set()
        return EventStream(hold())
    owner.agent.set_stream_fn(stream)
    async def send(event):
        if ending == 'delivery-fail' and event['type'] == 'extension_input_queued':
            raise OSError('Injected owned receipt failure')
        events.append(event)
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav, send=send)
    controller.negotiate({'extension_queued_inputs': 1})
    async def run():
        async with controller.bind('original-prompt'):
            await _handle_prompt(owner, 'Original model turn', events.append)
    task = asyncio.create_task(run())
    try:
        await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        controller.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                           'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        if ending == 'cancel':
            await wait(entered.is_set)
            task.cancel()
        async with asyncio.timeout(10):
            await asyncio.gather(task, return_exceptions=True)
        assert not owner.agent.pending_inputs() and not controller.queued_inputs._owned
        assert not any(message.role == 'user' and 'REVIEWED-QUEUE' in str(message.content)
                       for message in owner.messages)
        if ending == 'cancel':
            assert stopped.is_set()
            returned = [row for row in events if row['type'] == 'extension_input_returned']
            assert len(returned) == 1 and returned[0]['images'][0]['data'] == PNG
            assert returned[0]['content'].endswith('REVIEWED-QUEUE /quit')
        else:
            assert not any(row['type'] == 'extension_input_queued' for row in events)
            assert not any(row['type'] == 'extension_input_returned' for row in events)
        assert owner.permissions.mode == 'allow-all'
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await controller.aclose()
        await nav.aclose()
        await close(owner)


async def test_rpc_pending_status_returns_only_boolean_and_reader_is_revoked_after_original_binding(tmp_path):
    owner, calls = session(tmp_path, extra=('queuedhost',))
    events = []
    nav = RpcNavigation(owner, events.append)
    controller = RpcExtensions(owner, events.append, navigation=nav)
    controller.negotiate({'extension_queued_inputs': 1})
    try:
        controller.start({'request_id': 'pending', 'session_id': owner.session_id, 'action': 'command',
                          'name': 'ext-queuedhost-pending', 'args': ''}, turn_running=False)
        await controller.task
        reply = next(row for row in events if row['type'] == 'extension_result')
        assert reply['success'] and reply['output'] == 'PENDING-MESSAGES false'
        assert not any(row['type'] == 'extension_dialog' for row in events)
        assert owner.extensions.pending_messages is None and not calls and not owner.messages
        assert await owner.extensions.command('ext-queuedhost-pending', '') == 'PENDING-MESSAGES null'
    finally:
        await controller.aclose()
        await nav.aclose()
        await close(owner)
