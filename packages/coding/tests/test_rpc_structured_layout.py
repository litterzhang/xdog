"""Real layout workers exercise composed frames through actual parent/child RPC owners."""
from __future__ import annotations

import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.tui.utils import visible_width

from packages.coding.tests.test_rpc_child_custom import input_packet
from packages.coding.tests.test_rpc_children import close as close_child
from packages.coding.tests.test_rpc_children import control, setup, wait
from packages.coding.tests.test_rpc_children import packet as child_packet
from packages.coding.tests.test_rpc_custom_views import frame_input
from packages.coding.tests.test_rpc_extensions import close as close_parent
from packages.coding.tests.test_rpc_extensions import packet, response, session


@pytest.mark.parametrize('child', [False, True], ids=['parent', 'child'])
@pytest.mark.parametrize('presentation', ['inline', 'full', 'overlay'])
async def test_actual_rpc_layout_composition_resize_layers_viewport_and_nested_focus(tmp_path, child, presentation):
    mode = 'scroll ' + presentation
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, 'allow-all', 'layout')
        def adapter(model, context, options):
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='layout-call',
                name=next(tool.name for tool in context.tools if tool.name.startswith('ext_layout_view')),
                arguments={'mode': mode}),)) if context.messages[-1].role == 'user' else
                AssistantMessage(content=(TextContent(text='DONE'),)))
        owner.agent.set_stream_fn(adapter)
        controller.negotiate({'child_custom_views': 1, 'child_dialogs': 1})
        await control(controller, 'start', task='layout', access='inherit')
        dialog_type = 'child_extension_dialog'
    else:
        owner, _ = session(tmp_path, extra=('layout',))
        events = []
        controller = RpcExtensions(owner, events.append)
        controller.negotiate({'extension_custom_views': 1, 'extension_dialogs': 1})
        controller.start({**packet(owner, 'layout', 'ext-layout-view'), 'args': mode}, turn_running=False)
        dialog_type = 'extension_dialog'
    try:
        await wait(lambda: any(event['type'] == dialog_type for event in events))
        dialog = next(event for event in events if event['type'] == dialog_type)
        identifier = dialog['dialog']['id']
        assert dialog['dialog']['fullscreen'] is (presentation == 'full')
        if presentation == 'overlay':
            assert dialog['dialog']['overlay']['anchor'] == 'top-right'
            assert dialog['dialog']['overlay']['width'] == '75%'
        width, height, sequence, geometry = 80, 16, 0, 1
        async def send(kind='key', **fields):
            nonlocal sequence
            sequence += 1
            previous = controller.custom_frames.get(identifier)
            values = {'kind': kind, 'painted': previous['revision'] if previous else -1, **fields}
            request = (input_packet(controller, dialog, sequence, **values) if child else
                       frame_input(dialog, dialog['custom_generation'], sequence, **values))
            request['event'].update(width=width, height=height, generation=geometry)
            controller.custom_input(request)
            await wait(lambda: identifier in controller.custom_frames and (previous is None
                       or controller.custom_frames[identifier]['revision'] > previous['revision']))
            frame = controller.custom_frames[identifier]
            assert len(frame['lines']) <= height
            assert all(visible_width(line) <= width for line in frame['lines'])
            assert all('\x1b' not in line for line in frame['lines'])
            if frame['cursor'] is not None:
                row, col = frame['cursor']
                assert 0 <= row < height and 0 <= col < width
            return frame
        first = await send('resize')
        assert 'STRUCTURED-LAYOUT' in str(first['lines']) and 'Draft' in str(first['lines'])
        assert 'ROW-0000' in str(first['lines']) and first['cursor'] is not None
        # Exercise actual viewport vertical and horizontal movement, not just layout metadata.
        page = await send(key='pagedown')
        assert 'ROW-0020' in str(page['lines'])
        horizontal = await send(key='f3')
        assert 'PAN-0020' in str(horizontal['lines'])
        assert '界' in str(horizontal['lines'])
        await send(key='f4')
        await send('paste', text='开发🙂')
        layered = await send(key='f5')
        assert 'LAYER-POPUP' in str(layered['lines']) and layered['cursor'] is None
        # Layer is worker-owned focus: pasted text must not reach the obscured editor.
        await send('paste', text='MUST-NOT-EDIT')
        await send(key='f5')
        width, height, geometry = 24, 10, 2
        narrow = await send('resize')
        assert narrow['cursor'] is not None
        width, height, geometry = 80, 16, 3
        await send('resize')
        # Nested questions protect broker ownership before original view may continue.
        sequence += 1
        frame = controller.custom_frames[identifier]
        request = (input_packet(controller, dialog, sequence, kind='key', key='f2', painted=frame['revision']) if child
                   else frame_input(dialog, dialog['custom_generation'], sequence, kind='key', key='f2',
                                    painted=frame['revision']))
        request['event'].update(width=width, height=height, generation=geometry)
        controller.custom_input(request)
        await wait(lambda: len([event for event in events if event['type'] == dialog_type]) == 2)
        question = [event for event in events if event['type'] == dialog_type][-1]
        assert question['dialog']['parent_id'] == identifier
        with pytest.raises(ValueError):
            controller.custom_input(request)
        if child:
            controller.answer_dialog(child_packet(controller, child_id=dialog['child_id'],
                id=question['dialog']['id'], value='NESTED-LAYOUT-ANSWER'))
        else:
            controller.answer(response(question, 'NESTED-LAYOUT-ANSWER'))
        await wait(lambda: identifier in controller.custom_frames)
        frame = controller.custom_frames[identifier]
        sequence += 1
        request = (input_packet(controller, dialog, sequence, kind='key', painted=frame['revision']) if child else
                   frame_input(dialog, dialog['custom_generation'], sequence, kind='key', painted=frame['revision']))
        request['event'].update(width=width, height=height, generation=geometry)
        controller.custom_input(request)
        if child:
            await control(controller, 'wait', 'wait', child_id=dialog['child_id'])
            history = owner.children.supervisor.capture_source(dialog['child_id']).messages
            result = next(message for message in history if getattr(message, 'role', '') == 'toolResult')
            output = next(part.text for part in result.content if isinstance(part, TextContent))
        else:
            await controller.task
            output = next(event['output'] for event in events if event['type'] == 'extension_result')
        value = json.loads(output)
        assert value['value'] == {'text': '开发🙂', 'nested': 'NESTED-LAYOUT-ANSWER'}
        assert value['calls'] == 1 and not owner.messages
        assert owner.permissions.mode == ('allow-all' if child else 'deny')
    finally:
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)


def test_custom_rpc_sanitizer_preserves_grapheme_width_without_terminal_control_authority():
    from xdog.coding.modes.rpc.terminal_data import plain_custom_line
    from xdog.tui.utils import strip_ansi

    raw = '\x1b[36m界👩\u200d💻e\u0301\x1b[0m\x1b]52;c;private\a\x1b[2J\x1b_pi:c\a'
    clean = plain_custom_line(raw)
    assert clean == '界👩\u200d💻e\u0301'
    assert visible_width(clean) == visible_width(strip_ansi('界👩\u200d💻e\u0301')) == 5
    assert '\x1b' not in plain_custom_line('\x1bPprivate\x1b\\safe')
    assert plain_custom_line('a\t界\u202eb') == 'a   界b'
