"""Frame queue accounting and owned overflow cancellation, not permission changes."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.rpc.custom_delivery import CustomDelivery
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_custom_views import wait
from packages.coding.tests.test_rpc_extensions import close, packet, session


async def test_cross_thread_delivery_count_bytes_refusal_and_success_release(monkeypatch):
    delivery = CustomDelivery(asyncio.get_running_loop())
    queued = []
    monkeypatch.setattr(delivery.loop, 'call_soon_threadsafe', lambda *args: queued.append(args))
    seen = []
    for index in range(16):
        assert delivery.schedule(seen.append, (index,), 128 * 1024)
    assert delivery.pending == 16 and delivery.bytes == 2 * 1024 * 1024
    assert not delivery.schedule(seen.append, (17,), 1)
    for callback, *args in queued:
        callback(*args)
    assert seen == list(range(16)) and delivery.pending == delivery.bytes == 0
    assert not delivery.schedule(seen.append, ('too-large',), 2 * 1024 * 1024 + 1)


async def test_failure_and_closed_loop_release_reservations(monkeypatch):
    delivery = CustomDelivery(asyncio.get_running_loop())
    def fails(*args):
        raise RuntimeError('loop closed')
    monkeypatch.setattr(delivery.loop, 'call_soon_threadsafe', fails)
    assert not delivery.schedule(lambda: None, (), 100)
    assert delivery.pending == delivery.bytes == 0
    def bad_callback():
        raise ValueError('callback failed')
    delivery.pending, delivery.bytes = 1, 100
    with pytest.raises(ValueError):
        delivery._deliver(bad_callback, (), 100)
    assert delivery.pending == delivery.bytes == 0


async def test_actual_parent_worker_view_cancels_on_frame_queue_overflow_without_model_or_grant(tmp_path):
    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        identifier = dialog['dialog']['id']
        for index in range(20):
            controller._publish({'type': 'extension_custom_frame', 'id': identifier, 'extension': 'custom',
                'width': 40, 'height': 5, 'revision': index + 1, 'generation': 1,
                'lines': ['frame'], 'cursor': None, 'review_required': False})
        assert controller.custom_delivery.pending == 16
        async with asyncio.timeout(6):
            await controller.task
        await asyncio.sleep(0)
        result = next(event for event in events if event['type'] == 'extension_result')
        assert result['success'] and '"value": null' in result['output']
        errors = [event for event in events if event['type'] == 'extension_custom_error']
        assert len(errors) == 1 and errors[0]['code'] == 'frame_delivery_capacity'
        assert errors[0]['canceled'] is True and errors[0]['id'] == identifier
        assert not owner.extension_dialogs.has_pending and not owner.extension_dialogs.custom.views
        assert controller.custom_delivery.pending == controller.custom_delivery.bytes == 0
        assert not calls and not owner.messages and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


async def test_actual_child_worker_view_cancels_on_frame_queue_overflow_and_keeps_parent_policy(tmp_path):
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    from packages.coding.tests.test_rpc_children import close as close_child
    from packages.coding.tests.test_rpc_children import control, setup
    owner, _, nav, sources, controller, events = setup(tmp_path, 'allow-all', 'custom')
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='custom-call',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
            arguments={}),)) if context.messages[-1].role == 'user' else
            AssistantMessage(content=(TextContent(text='DONE'),)))
    owner.agent.set_stream_fn(adapter)
    try:
        controller.negotiate({'child_custom_views': 1})
        await control(controller, 'start', task='view', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'child_extension_dialog')
        identifier = dialog['dialog']['id']
        for index in range(20):
            owner.children.supervisor.publish({'type': 'child_extension_custom_frame', 'child_id': child,
                'id': identifier, 'extension': 'custom', 'width': 40, 'height': 5,
                'revision': index + 1, 'generation': 1, 'lines': ['frame'], 'cursor': None,
                'review_required': False})
        assert controller.custom_delivery.pending == 16
        await control(controller, 'wait', 'wait', child_id=child)
        await asyncio.sleep(0)
        history = owner.children.supervisor.capture_source(child).messages
        assert '"value": null' in str(history) and 'DONE' in str(history)
        errors = [event for event in events if event['type'] == 'child_extension_custom_error']
        assert len(errors) == 1 and errors[0]['code'] == 'frame_delivery_capacity'
        assert errors[0]['child_id'] == child and errors[0]['child_owner'] == controller.lease
        assert controller.custom_delivery.pending == controller.custom_delivery.bytes == 0
        assert not owner.messages and owner.permissions.mode == 'allow-all'
    finally:
        await close_child(owner, nav, sources, controller)


async def test_failure_reports_are_deduplicated_bounded_and_released(monkeypatch):
    delivery = CustomDelivery(asyncio.get_running_loop())
    queued, seen = [], []
    monkeypatch.setattr(delivery.loop, 'call_soon_threadsafe', lambda callback: queued.append(callback))
    for index in range(8):
        key = (str(index),)
        assert delivery.report_failure(key, seen.append, (index,))
        assert not delivery.report_failure(key, seen.append, (index,))
    assert not delivery.report_failure(('overflow',), seen.append, (9,))
    assert len(queued) == len(delivery.failures) == 8
    for callback in queued:
        callback()
    assert seen == list(range(8)) and not delivery.failures


async def test_stale_parent_overflow_report_does_not_touch_replacement_view(tmp_path):
    owner, _ = session(tmp_path)
    events = []
    controller = RpcExtensions(owner, events.append)
    try:
        controller.negotiate({'extension_custom_views': 1})
        async with controller.bind('old'):
            old_scope = controller.scope
            old_generation = controller.custom_generation
        async with controller.bind('replacement'):
            frame = {'revision': 2}
            controller.custom_frames['same-id'] = frame
            controller._frame_overflow(old_scope, old_generation, 'same-id')
            assert controller.custom_frames['same-id'] is frame
            assert not any(event['type'] == 'extension_custom_error' for event in events)
            current_scope = controller.scope
            controller.negotiate({'extension_custom_views': 0})
            controller.negotiate({'extension_custom_views': 1})
            controller.custom_frames['same-id'] = frame
            controller._frame_overflow(current_scope, old_generation, 'same-id')
            assert controller.custom_frames['same-id'] is frame
            assert not any(event['type'] == 'extension_custom_error' for event in events)
    finally:
        await controller.aclose()
        await close(owner)


async def test_stale_child_overflow_report_cannot_clear_frame_after_capability_reenable(tmp_path):
    from packages.coding.tests.test_rpc_children import close as close_child
    from packages.coding.tests.test_rpc_children import setup
    owner, _, nav, sources, controller, events = setup(tmp_path)
    try:
        controller.negotiate({'child_custom_views': 1})
        generation, bound, binding = controller.custom_generation, controller.bound, controller.binding
        controller.negotiate({'child_custom_views': 0})
        controller.negotiate({'child_custom_views': 1})
        frame = {'revision': 2}
        controller.custom_frames['same-id'] = frame
        controller._frame_overflow(bound, binding, generation, 'a' * 12, 'same-id')
        assert controller.custom_frames['same-id'] is frame
        assert not any(event['type'] == 'child_extension_custom_error' for event in events)
    finally:
        await close_child(owner, nav, sources, controller)


async def test_parent_output_refusal_cancels_actual_view_without_cache_or_queued_memory(tmp_path):
    from packages.coding.tests.test_rpc_custom_views import frame_input
    owner, calls = session(tmp_path, extra=('custom',))
    events = []
    def sink(event):
        if event['type'] == 'extension_custom_frame':
            raise OSError('fixture output backlog refused')
        events.append(event)
    controller = RpcExtensions(owner, sink)
    try:
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == 'extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'extension_dialog')
        controller.custom_input(frame_input(dialog, dialog['custom_generation'], 1))
        async with asyncio.timeout(6):
            await controller.task
        assert not controller.custom_frames and not owner.extension_dialogs.has_pending
        assert controller.custom_delivery.pending == controller.custom_delivery.bytes == 0
        assert '"value": null' in next(event['output'] for event in events if event['type'] == 'extension_result')
        assert not calls and not owner.messages and owner.permissions.mode == 'deny'
    finally:
        await controller.aclose()
        await close(owner)


async def test_child_output_refusal_cancels_actual_view_without_borrowed_parent_authority(tmp_path):
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    from packages.coding.tests.test_rpc_child_custom import input_packet
    from packages.coding.tests.test_rpc_children import close as close_child
    from packages.coding.tests.test_rpc_children import control, setup
    owner, _, nav, sources, controller, events = setup(tmp_path, 'allow-all', 'custom')
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='custom-call',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
            arguments={}),)) if context.messages[-1].role == 'user' else
            AssistantMessage(content=(TextContent(text='DONE'),)))
    def sink(event):
        if event['type'] == 'child_extension_custom_frame':
            raise OSError('fixture output backlog refused')
        events.append(event)
    controller.emit = sink
    owner.agent.set_stream_fn(adapter)
    try:
        controller.negotiate({'child_custom_views': 1})
        await control(controller, 'start', task='view', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'child_extension_dialog')
        controller.custom_input(input_packet(controller, dialog, 1))
        await control(controller, 'wait', 'wait', child_id=child)
        assert not controller.custom_frames
        assert controller.custom_delivery.pending == controller.custom_delivery.bytes == 0
        assert '"value": null' in str(owner.children.supervisor.capture_source(child).messages)
        assert not owner.messages and owner.permissions.mode == 'allow-all'
    finally:
        await close_child(owner, nav, sources, controller)


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
async def test_actual_custom_input_is_blocked_by_unpainted_approval_without_retiring_frame(tmp_path, child):
    from xdog.agent import AgentContext, BeforeToolCallContext
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream

    from packages.coding.tests.test_rpc_child_custom import input_packet
    from packages.coding.tests.test_rpc_children import close as close_child
    from packages.coding.tests.test_rpc_children import control, setup
    from packages.coding.tests.test_rpc_custom_views import frame_input
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, 'allow-all', 'custom')
        def adapter(model, context, options):
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='view',
                name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
                arguments={}),)) if context.messages[-1].role == 'user' else
                AssistantMessage(content=(TextContent(text='DONE'),)))
        owner.agent.set_stream_fn(adapter)
        controller.negotiate({'child_custom_views': 1})
        await control(controller, 'start', task='view', access='inherit')
        kind = 'child_extension_dialog'
    else:
        owner, _ = session(tmp_path, extra=('custom',))
        events = []
        controller = RpcExtensions(owner, events.append)
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'view', 'ext-custom-view'), turn_running=False)
        kind = 'extension_dialog'
    approval = None
    try:
        await wait(lambda: any(event['type'] == kind for event in events))
        dialog = next(event for event in events if event['type'] == kind)
        identifier = dialog['dialog']['id']
        initial = input_packet(controller, dialog, 1) if child else frame_input(dialog, dialog['custom_generation'], 1)
        controller.custom_input(initial)
        await wait(lambda: identifier in controller.custom_frames)
        frame = controller.custom_frames[identifier]
        owner.permissions.set_mode('ask')
        owner.permissions.set_request_handler(lambda request: None)
        call = ToolCall(id='approval', name='filesystem', arguments={'action': 'write', 'path': 'target'})
        approval = asyncio.create_task(owner.permissions.before_tool_call(BeforeToolCallContext(
            assistant_message=AssistantMessage(), tool_call=call, args=call.arguments, context=AgentContext())))
        await wait(lambda: owner.permissions.has_pending_requests)
        action = (input_packet(controller, dialog, 2, kind='key', painted=frame['revision']) if child else
                  frame_input(dialog, dialog['custom_generation'], 2, kind='key', painted=frame['revision']))
        with pytest.raises(ValueError, match='approval owns input'):
            controller.custom_input(action)
        assert controller.custom_frames[identifier] is frame
        assert controller.custom_sequences[identifier] == 1
        owner.permissions.deny_all()
        assert await approval is not None
        assert not (tmp_path / 'target').exists() and not owner.messages
    finally:
        owner.permissions.deny_all()
        if approval is not None:
            await approval
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close(owner)
