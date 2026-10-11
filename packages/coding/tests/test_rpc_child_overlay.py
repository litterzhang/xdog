"""Child live overlays preserve separate permission and input ownership."""
from __future__ import annotations

import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_rpc_child_custom import input_packet
from packages.coding.tests.test_rpc_children import close, control, setup, wait


@pytest.mark.parametrize('outcome', ['complete', 'withdraw'])
async def test_actual_child_overlay_controls_require_focus_and_new_painted_generation(tmp_path, outcome):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'overlaymock')
    def adapter(model, context, options):
        message = (AssistantMessage(content=(ToolCall(id='overlay-call',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_overlaymock_view')),
            arguments={'mode': 'auto'}),)) if context.messages[-1].role == 'user' else
            AssistantMessage(content=(TextContent(text='DONE'),)))
        return EventStream.empty(message)
    session.agent.set_stream_fn(adapter)
    try:
        children.negotiate({'child_custom_views': 1, 'child_overlay_controls': 1})
        await control(children, 'start', task='overlay', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'child_extension_dialog')
        children.custom_input(input_packet(children, dialog, 1))
        await wait(lambda: children.custom_frames)
        first = children.custom_frames[dialog['dialog']['id']]
        with pytest.raises(ValueError, match='non-capturing'):
            children.custom_input(input_packet(children, dialog, 2, kind='key', painted=first['revision']))
        await wait(lambda: any(event['type'] == 'child_extension_custom_control' and event['state']['hidden']
                              for event in events))
        with pytest.raises(ValueError):
            children.custom_input(input_packet(children, dialog, 3, kind='key', painted=first['revision']))
        if outcome == 'withdraw':
            old = children.custom_generation
            children.negotiate({'child_overlay_controls': 0})
            assert children.custom_generation > old
            with pytest.raises(ValueError):
                children.custom_input({**input_packet(children, dialog, 4), 'custom_generation': old})
        else:
            await wait(lambda: any(event['type'] == 'child_extension_custom_control' and event['state']['capturing']
                                  for event in events))
            await wait(lambda: children.custom_frames)
            current = children.custom_frames[dialog['dialog']['id']]
            request = input_packet(children, dialog, 4)
            request['event']['generation'] = current['generation'] + 1
            children.custom_input(request)
            await wait(lambda: dialog['dialog']['id'] in children.custom_frames
                   and children.custom_frames[dialog['dialog']['id']]['revision'] > current['revision'])
            fresh = children.custom_frames[dialog['dialog']['id']]
            request = input_packet(children, dialog, 5, kind='key', painted=fresh['revision'])
            request['event']['generation'] = fresh['generation']
            children.custom_input(request)
        await control(children, 'wait', 'wait', child_id=child)
        history = session.children.supervisor.capture_source(child).messages
        assert ('"inputs": ["enter"]' in str(history)) is (outcome == 'complete')
        assert not session.messages and session.permissions.mode == 'allow-all'
    finally:
        await close(session, nav, sources, children)


async def test_child_overlay_control_capability_is_not_implied_by_custom_view_support(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'overlaymock')
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='overlay-call',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_overlaymock_view')),
            arguments={}),)) if context.messages[-1].role == 'user' else
            AssistantMessage(content=(TextContent(text='DONE'),)))
    session.agent.set_stream_fn(adapter)
    try:
        children.negotiate({'child_custom_views': 1})
        await control(children, 'start', task='overlay', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await control(children, 'wait', 'wait', child_id=child)
        assert not any(event['type'] == 'child_extension_dialog' for event in events)
        assert not children.flags['child_overlay_controls']
    finally:
        await close(session, nav, sources, children)


def test_actual_cli_child_overlay_requires_separate_tool_approval_and_focused_frame(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/overlaymock', root / 'overlaymock')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['overlaymock'], 'extension_dir': str(root)}))
    monkeypatch.setenv('XDOG_RPC_CHILD_OVERLAY', '1')
    peer = Peer(tmp_path, write=True)
    try:
        peer.send({'type': 'rpc_hello', 'child_custom_views': 1, 'child_overlay_controls': 1})
        caps = peer.read('child_capabilities')
        while not caps['child_overlay_controls']:
            caps = peer.read('child_capabilities')
        peer.caps = caps
        peer.send(peer.packet('start', action='start', task='overlay', access='inherit'))
        child = peer.read('child_result')
        assert child['success']
        approval = peer.read('child_permission_request')
        peer.send(peer.packet('approval', type='child_permission_response', id=approval['id'],
            child_id=child['child_id'], decision='allow_once', approval_generation=approval['approval_generation']))
        peer.read('child_permission_ack')
        dialog = peer.read('child_extension_dialog')
        def send(sequence, *, kind='resize', painted=-1, generation=1):
            peer.send(peer.packet('view-input', type='child_custom_input', id=dialog['dialog']['id'],
                child_id=child['child_id'], custom_generation=dialog['custom_generation'], sequence=sequence,
                event={'kind': kind, 'width': 40, 'height': 5, 'painted': painted, 'generation': generation,
                    **({'key': 'enter', 'ctrl': False, 'alt': False, 'shift': False} if kind == 'key' else {})}))
        send(1)
        first = peer.read('child_extension_custom_frame')
        send(2, kind='key', painted=first['revision'])
        assert peer.read('error')['action'] == 'child_custom_input'
        hidden = peer.read('child_extension_custom_control')
        shown = peer.read('child_extension_custom_control')
        focused = peer.read('child_extension_custom_control')
        assert hidden['state']['hidden'] and not shown['state']['hidden']
        assert not shown['state']['capturing'] and focused['state']['capturing']
        assert focused['child_id'] == child['child_id'] and focused['child_owner'] == caps['child_owner']
        send(3, generation=2)
        fresh = peer.read('child_extension_custom_frame')
        while fresh['generation'] != 2:
            fresh = peer.read('child_extension_custom_frame')
        send(4, kind='key', painted=fresh['revision'], generation=2)
        peer.send(peer.packet('wait', action='wait', child_id=child['child_id']))
        assert peer.read('child_result')['success']
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'ask'
        assert not (tmp_path / 'cli-child-target').exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()
