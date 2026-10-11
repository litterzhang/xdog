"""Actual worker background repaints flow through parent and child RPC frame owners."""
from __future__ import annotations

import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.rpc.extensions import RpcExtensions

from packages.coding.tests.test_rpc_child_custom import input_packet
from packages.coding.tests.test_rpc_children import close as close_child
from packages.coding.tests.test_rpc_children import control, setup, wait
from packages.coding.tests.test_rpc_custom_views import frame_input
from packages.coding.tests.test_rpc_extensions import close as close_parent
from packages.coding.tests.test_rpc_extensions import packet, session


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
async def test_actual_worker_background_refresh_repaints_without_fabricating_input(tmp_path, child):
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, 'allow-all', 'liveview')
        def adapter(model, context, options):
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='live-call',
                name=next(tool.name for tool in context.tools if tool.name.startswith('ext_liveview_view')),
                arguments={}),)) if context.messages[-1].role == 'user' else
                AssistantMessage(content=(TextContent(text='DONE'),)))
        owner.agent.set_stream_fn(adapter)
        controller.negotiate({'child_custom_views': 1})
        await control(controller, 'start', task='live view', access='inherit')
        dialog_type, frame_type = 'child_extension_dialog', 'child_extension_custom_frame'
    else:
        owner, _ = session(tmp_path, extra=('liveview',))
        events = []
        controller = RpcExtensions(owner, events.append)
        controller.negotiate({'extension_custom_views': 1})
        controller.start(packet(owner, 'live', 'ext-liveview-view'), turn_running=False)
        dialog_type, frame_type = 'extension_dialog', 'extension_custom_frame'
    try:
        await wait(lambda: any(event['type'] == dialog_type for event in events))
        dialog = next(event for event in events if event['type'] == dialog_type)
        identifier = dialog['dialog']['id']
        def send(sequence, **fields):
            controller.custom_input(input_packet(controller, dialog, sequence, **fields) if child else
                                    frame_input(dialog, dialog['custom_generation'], sequence, **fields))
        send(1)
        await wait(lambda: identifier in controller.custom_frames)
        first = controller.custom_frames[identifier]
        await wait(lambda: any(event['type'] == frame_type and 'LIVE-STATE=2;INPUTS=0' in str(event['lines'])
                              for event in events))
        fresh = controller.custom_frames[identifier]
        assert fresh['revision'] > first['revision']
        with pytest.raises(ValueError):
            send(2, kind='key', painted=first['revision'])
        send(3, kind='key', painted=fresh['revision'])
        if child:
            await control(controller, 'wait', 'wait', child_id=dialog['child_id'])
            history = owner.children.supervisor.capture_source(dialog['child_id']).messages
            message = next(message for message in history if getattr(message, 'role', '') == 'toolResult')
            output = next(part.text for part in message.content if isinstance(part, TextContent))
        else:
            await controller.task
            output = next(event['output'] for event in events if event['type'] == 'extension_result')
        assert json.loads(output)['value'] == {'state': 2, 'inputs': 1}
        assert not owner.messages and owner.permissions.mode == ('allow-all' if child else 'deny')
    finally:
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)
