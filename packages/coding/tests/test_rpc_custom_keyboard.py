"""Actual custom workers enforce declared key phases and preserve atomic pasted text."""
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
@pytest.mark.parametrize('rich', [False, True], ids=['press-only', 'enhanced'])
async def test_actual_parent_and_child_custom_key_phases_and_unicode_atomic_paste(tmp_path, child, rich):
    if child:
        owner, _, nav, sources, controller, events = setup(tmp_path, 'allow-all', 'custom')
        def adapter(model, context, options):
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='keyboard-call',
                name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
                arguments={'mode': 'rich' if rich else ''}),)) if context.messages[-1].role == 'user' else
                AssistantMessage(content=(TextContent(text='DONE'),)))
        owner.agent.set_stream_fn(adapter)
        controller.negotiate({'child_custom_views': 1})
        await control(controller, 'start', task='keyboard', access='inherit')
        dialog_type, frame_type = 'child_extension_dialog', 'child_extension_custom_frame'
    else:
        owner, _ = session(tmp_path, extra=('custom',))
        events = []
        controller = RpcExtensions(owner, events.append)
        controller.negotiate({'extension_custom_views': 1})
        controller.start({**packet(owner, 'keyboard', 'ext-custom-view'), 'args': 'rich' if rich else ''},
                         turn_running=False)
        dialog_type, frame_type = 'extension_dialog', 'extension_custom_frame'
    try:
        await wait(lambda: any(event['type'] == dialog_type for event in events))
        dialog = next(event for event in events if event['type'] == dialog_type)
        identifier = dialog['dialog']['id']
        assert dialog['dialog']['key_events'] == (['press', 'repeat', 'release'] if rich else ['press'])
        assert dialog['dialog']['fullscreen'] is rich
        def send(sequence, **fields):
            value = (input_packet(controller, dialog, sequence, **fields) if child else
                     frame_input(dialog, dialog['custom_generation'], sequence, **fields))
            controller.custom_input(value)
        send(1)
        await wait(lambda: identifier in controller.custom_frames)
        first = controller.custom_frames[identifier]
        phases = ('repeat', 'release')
        if not rich:
            for phase in phases:
                with pytest.raises(ValueError):
                    send(2, kind='key', event_type=phase, painted=first['revision'])
                assert controller.custom_frames[identifier] is first
            phases = ()
        sequence = 2
        expected = []
        for phase in phases:
            frame = controller.custom_frames[identifier]
            send(sequence, kind='key', event_type=phase, painted=frame['revision'])
            await wait(lambda: identifier in controller.custom_frames
                       and controller.custom_frames[identifier]['revision'] > frame['revision'])
            assert not any(event['type'] == 'extension_result' for event in events)
            expected.append(phase + ':enter')
            sequence += 1
        # A complete paste is one callback; newline/Unicode are data, never a command/keypress sequence.
        paste = '开发🙂\nsecond line\tquoted "text"'
        frame = controller.custom_frames[identifier]
        send(sequence, kind='paste', text=paste, painted=frame['revision'])
        await wait(lambda: identifier in controller.custom_frames
                   and controller.custom_frames[identifier]['revision'] > frame['revision'])
        expected.append(paste)
        assert not any(event['type'] == 'extension_result' for event in events)
        frame = controller.custom_frames[identifier]
        sequence += 1
        send(sequence, kind='key', event_type='press', painted=frame['revision'])
        expected.append('press:enter' if rich else 'enter')
        if child:
            await control(controller, 'wait', 'wait', child_id=dialog['child_id'])
            capture = owner.children.supervisor.capture_source(dialog['child_id'])
            result = next(message for message in capture.messages if getattr(message, 'role', '') == 'toolResult')
            output = next(part.text for part in result.content if isinstance(part, TextContent))
        else:
            await controller.task
            output = next(event['output'] for event in events if event['type'] == 'extension_result')
        decoded = json.loads(output)
        assert decoded['inputs'] == expected and decoded['value'] == {'selected': False, 'count': 0, 'mode': None}
        assert decoded['calls'] == 1
        assert not owner.messages and owner.permissions.mode == ('allow-all' if child else 'deny')
        assert all('\x1b' not in ''.join(event['lines']) for event in events if event['type'] == frame_type)
    finally:
        if child:
            await close_child(owner, nav, sources, controller)
        else:
            await controller.aclose()
            await close_parent(owner)


@pytest.mark.parametrize('rich', [False, True], ids=['press-only', 'enhanced'])
def test_actual_cli_custom_keyboard_phases_and_atomic_multiline_paste(tmp_path, rich):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/custom', root / 'custom')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
    peer = Peer(tmp_path)
    try:
        peer.send({'type': 'rpc_hello', 'extension_custom_views': 1})
        peer.read('extension_custom_capabilities')
        peer.send({'type': 'extension_control', 'request_id': 'keyboard', 'session_id': peer.ready['session_id'],
                   'action': 'command', 'name': 'ext-custom-view', 'args': 'rich' if rich else ''})
        dialog = peer.read('extension_dialog')
        generation = dialog['custom_generation']
        peer.send(frame_input(dialog, generation, 1))
        frame = peer.read('extension_custom_frame')
        sequence, expected = 2, []
        for phase in ('repeat', 'release'):
            peer.send(frame_input(dialog, generation, sequence, kind='key', event_type=phase,
                                  painted=frame['revision']))
            if rich:
                frame = peer.read('extension_custom_frame')
                expected.append(phase + ':enter')
            else:
                assert peer.read('error')['action'] == 'extension_custom_input'
            sequence += 1
        paste = '开发🙂\nsecond line\tquoted "text"'
        peer.send(frame_input(dialog, generation, sequence, kind='paste', text=paste, painted=frame['revision']))
        frame = peer.read('extension_custom_frame')
        expected.append(paste)
        peer.send(frame_input(dialog, generation, sequence + 1, kind='key', event_type='press',
                              painted=frame['revision']))
        expected.append('press:enter' if rich else 'enter')
        result = peer.read('extension_result')
        assert result['success']
        assert json.loads(result['output'])['inputs'] == expected
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'deny' and not peer.marker.exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()
