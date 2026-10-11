"""Custom callback contributions use separate transient grants, not frame authority."""
from __future__ import annotations

import json
from pathlib import Path

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
async def test_actual_custom_callback_contributions_order_withdrawal_and_no_history(tmp_path, child):
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, 'allow-all', 'custom')
        def adapter(model, context, options):
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='custom-call',
                name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
                arguments={}),)) if context.messages[-1].role == 'user' else
                AssistantMessage(content=(TextContent(text='DONE'),)))
        owner.agent.set_stream_fn(adapter)
        contribution = 'child_contribution'
        dialog_kind, frame_kind = 'child_extension_dialog', 'child_extension_custom_frame'
    else:
        owner, _ = session(tmp_path, extra=('custom',))
        events = []
        controller = RpcExtensions(owner, events.append)
        contribution = 'extension_contribution'
        dialog_kind, frame_kind = 'extension_dialog', 'extension_custom_frame'
    # Change only the owned temporary fixture, before any extension worker starts.
    source = Path(tmp_path / 'extensions/custom/main.py')
    source.write_text(source.read_text().replace('            inputs.append(',
        "            api.notify('CUSTOM-NOTICE token=PRIVATE')\n"
        "            api.status('callback', 'CUSTOM-STATUS')\n"
        "            api.widget('callback_widget', ['CUSTOM-WIDGET'])\n"
        '            inputs.append('))
    try:
        if child:
            controller.negotiate({'child_custom_views': 1, 'child_contributions': 1})
            await control(controller, 'start', task='custom view', access='inherit')
        else:
            controller.negotiate({'extension_custom_views': 1, 'extension_contributions': 1})
            controller.start(packet(owner, 'custom', 'ext-custom-view'), turn_running=False)
        await wait(lambda: any(event['type'] == dialog_kind for event in events))
        dialog = next(event for event in events if event['type'] == dialog_kind)
        identifier = dialog['dialog']['id']
        def send(sequence, **fields):
            controller.custom_input(input_packet(controller, dialog, sequence, **fields) if child else
                                    frame_input(dialog, dialog['custom_generation'], sequence, **fields))
        send(1)
        await wait(lambda: identifier in controller.custom_frames)
        frame = controller.custom_frames[identifier]
        send(2, kind='key', key='a', painted=frame['revision'])
        await wait(lambda: identifier in controller.custom_frames
                   and controller.custom_frames[identifier]['revision'] > frame['revision'])
        records = [event for event in events if event['type'] == contribution]
        assert len(records) == 3
        assert 'PRIVATE' not in json.dumps(records)
        assert all(event['retained'] is False for event in records)
        assert 'CUSTOM-WIDGET' in json.dumps(records) and 'CUSTOM-STATUS' in json.dumps(records)
        returned = next(event for event in events if event['type'] == frame_kind
                        and event['revision'] > frame['revision'])
        assert all(events.index(record) < events.index(returned) for record in records)
        # Withdrawal is contribution-only: it must not cancel custom frame/input authority.
        controller.negotiate({'child_contributions' if child else 'extension_contributions': 0})
        frame = controller.custom_frames[identifier]
        send(3, kind='key', key='enter', painted=frame['revision'])
        if child:
            await control(controller, 'wait', 'wait', child_id=dialog['child_id'])
            history = owner.children.supervisor.capture_source(dialog['child_id']).messages
            assert 'CUSTOM-NOTICE' not in str(history) and 'CUSTOM-WIDGET' not in str(history)
        else:
            await controller.task
            result = next(event for event in events if event['type'] == 'extension_result')
            assert result['success'] and json.loads(result['output'])['inputs'] == ['a', 'enter']
        assert len([event for event in events if event['type'] == contribution]) == 3
        assert not owner.messages and owner.permissions.mode == ('allow-all' if child else 'deny')
    finally:
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)
