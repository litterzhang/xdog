"""Real child tools use independently owned RPC custom frames, not parent approvals."""
from __future__ import annotations

import json

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream

from packages.coding.tests.test_rpc_children import close, control, packet, setup, wait


def input_packet(children, dialog, sequence, *, kind='resize', painted=-1, **fields):
    return packet(children, 'custom-input', child_id=dialog['child_id'], id=dialog['dialog']['id'],
                  custom_generation=children.custom_generation, sequence=sequence,
                  event={'kind': kind, 'width': 40, 'height': 5, 'painted': painted, 'generation': 1,
                         **({'key': 'enter', 'ctrl': False, 'alt': False, 'shift': False} if kind == 'key' else {}),
                         **fields})


@pytest.mark.parametrize('outcome', ['complete', 'withdraw', 'cancel', 'foreign'])
async def test_real_child_custom_tool_frame_completion_owner_and_cancel(tmp_path, outcome):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'custom')
    executed = []
    def adapter(model, context, options):
        executed.append(context)
        message = (AssistantMessage(content=(ToolCall(id='custom-call', name=next(tool.name for tool in context.tools
                              if tool.name.startswith('ext_custom_view')), arguments={}),))
                   if context.messages[-1].role == 'user' else
                   AssistantMessage(content=(TextContent(text='DONE'),)))
        return EventStream.empty(message)
    session.agent.set_stream_fn(adapter)
    try:
        children.negotiate({'child_custom_views': 1})
        await control(children, 'start', task='custom tool', access='inherit')
        started = next(event for event in events if event['type'] == 'child_result')
        child = started['child_id']
        await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'child_extension_dialog')
        assert dialog['dialog']['kind'] == 'custom' and dialog['custom_generation'] == children.custom_generation
        with pytest.raises(ValueError):
            children.answer_dialog(packet(children, child_id=child, id=dialog['dialog']['id'], value={'forged': True}))
        children.custom_input(input_packet(children, dialog, 1))
        await wait(lambda: dialog['dialog']['id'] in children.custom_frames)
        frame = children.custom_frames[dialog['dialog']['id']]
        assert '\x1b' not in ''.join(frame['lines'])
        if outcome == 'complete':
            with pytest.raises(ValueError):
                children.custom_input(input_packet(children, dialog, 2, kind='key', painted=0))
            children.custom_input(input_packet(children, dialog, 3, kind='key', painted=frame['revision']))
        elif outcome == 'withdraw':
            children.negotiate({'child_custom_views': 0})
            with pytest.raises(ValueError):
                children.custom_input(input_packet(children, dialog, 2))
        else:
            if outcome == 'foreign':
                for field in ('child_id', 'child_owner', 'child_epoch', 'session_revision', 'custom_generation'):
                    with pytest.raises(ValueError):
                        children.custom_input({**input_packet(children, dialog, 2), field: 'foreign'})
            children.answer_dialog(packet(children, child_id=child, id=dialog['dialog']['id'], value=None))
        await control(children, 'wait', 'wait', child_id=child)
        retained = session.children.supervisor.capture_source(child)
        assert len(executed) == 2 and 'DONE' in str(retained.messages)
        assert not session.messages and session.permissions.mode == 'allow-all'
        value = json.dumps([getattr(message, 'content', '') for message in retained.messages], default=str)
        assert ('selected' in value) is (outcome == 'complete')
    finally:
        await close(session, nav, sources, children)


@pytest.mark.parametrize('enabled', [False, True])
async def test_custom_child_capability_is_independent_of_parent_dialogs_and_source_grants(tmp_path, enabled):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'custom')
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(
            ToolCall(id='custom-call', name=next(tool.name for tool in context.tools
                              if tool.name.startswith('ext_custom_view')), arguments={}),))
            if context.messages[-1].role == 'user' else AssistantMessage(content=(TextContent(text='DONE'),)))
    session.agent.set_stream_fn(adapter)
    try:
        children.negotiate({'child_custom_views': int(enabled), 'child_dialogs': 0})
        await control(children, 'start', task='work', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        if enabled:
            await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
            dialog = next(event for event in events if event['type'] == 'child_extension_dialog')
            children.answer_dialog(packet(children, child_id=child, id=dialog['dialog']['id'], value=None))
        await control(children, 'wait', 'wait', child_id=child)
        assert any(event['type'] == 'child_extension_dialog' for event in events) is enabled
    finally:
        await close(session, nav, sources, children)


def test_actual_cli_child_custom_frames_are_independent_of_parent_dialog_or_tool_authority(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    from packages.coding.tests.test_rpc_children_cli import Peer
    home = tmp_path / 'home'
    home.mkdir()
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/custom', root / 'custom')
    (home / 'settings.json').write_text(json.dumps({'extensions': ['custom'], 'extension_dir': str(root)}))
    monkeypatch.setenv('XDOG_RPC_CHILD_CUSTOM', '1')
    monkeypatch.setenv('XDOG_RPC_PERMISSION', 'allow-all')
    # Peer supplies its own permission environment; use explicit child approval instead.
    peer = Peer(tmp_path, write=True)
    try:
        peer.send({'type': 'rpc_hello', 'child_custom_views': 1})
        caps = peer.read('child_capabilities')
        while not caps['child_custom_views']:
            caps = peer.read('child_capabilities')
        peer.caps = caps
        peer.send(peer.packet('start', action='start', task='custom', access='inherit'))
        started = peer.read('child_result')
        assert started['success']
        permission = peer.read('child_permission_request')
        peer.send(peer.packet('answer', type='child_permission_response', id=permission['id'],
            child_id=started['child_id'], decision='allow_once', approval_generation=permission['approval_generation']))
        peer.read('child_permission_ack')
        dialog = peer.read('child_extension_dialog')
        assert dialog['child_id'] == started['child_id'] and dialog['dialog']['kind'] == 'custom'
        def send(sequence, kind='resize', painted=-1):
            peer.send(peer.packet('custom', type='child_custom_input', child_id=started['child_id'],
                id=dialog['dialog']['id'], custom_generation=dialog['custom_generation'], sequence=sequence,
                event={'kind': kind, 'width': 40, 'height': 5, 'painted': painted, 'generation': 1,
                    **({'key': 'enter', 'ctrl': False, 'alt': False, 'shift': False} if kind == 'key' else {})}))
        send(1)
        frame = peer.read('child_extension_custom_frame')
        assert frame['child_owner'] == caps['child_owner'] and '\x1b' not in ''.join(frame['lines'])
        send(2, 'key', 0)
        assert peer.read('error')['action'] == 'child_custom_input'
        send(3, 'key', frame['revision'])
        peer.send(peer.packet('wait', action='wait', child_id=started['child_id']))
        result = peer.read('child_result')
        assert result['success']
        peer.send({'type': 'status'})
        status = peer.read('status')
        assert status['messages'] == 0 and status['permission_mode'] == 'ask'
        assert not (tmp_path / 'cli-child-target').exists()
        peer.send({'type': 'quit'})
        assert peer.process.wait(timeout=5) == 0
    finally:
        peer.close()


async def test_actual_child_nested_question_owns_input_and_parent_requires_fresh_frame(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'nested')
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='nested-call',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_nested_view')),
            arguments={}),)) if context.messages[-1].role == 'user' else
            AssistantMessage(content=(TextContent(text='DONE'),)))
    session.agent.set_stream_fn(adapter)
    try:
        children.negotiate({'child_custom_views': 1, 'child_dialogs': 1})
        await control(children, 'start', task='nested view', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
        parent = next(event for event in events if event['type'] == 'child_extension_dialog')
        children.custom_input(input_packet(children, parent, 1))
        await wait(lambda: parent['dialog']['id'] in children.custom_frames)
        first = children.custom_frames[parent['dialog']['id']]
        children.custom_input(input_packet(children, parent, 2, kind='key', key='n', painted=first['revision']))
        await wait(lambda: len([event for event in events if event['type'] == 'child_extension_dialog']) == 2)
        question = [event for event in events if event['type'] == 'child_extension_dialog'][-1]
        assert question['dialog']['parent_id'] == parent['dialog']['id']
        with pytest.raises(ValueError):
            children.custom_input(input_packet(children, parent, 3, kind='key', painted=first['revision']))
        children.answer_dialog(packet(children, child_id=child, id=question['dialog']['id'], value='CHILD-ANSWER'))
        await wait(lambda: len([event for event in events if event['type'] == 'child_extension_dialog']) == 3)
        confirm = [event for event in events if event['type'] == 'child_extension_dialog'][-1]
        children.answer_dialog(packet(children, child_id=child, id=confirm['dialog']['id'], value=True))
        await wait(lambda: parent['dialog']['id'] in children.custom_frames
                   and children.custom_frames[parent['dialog']['id']]['revision'] > first['revision'])
        restored = children.custom_frames[parent['dialog']['id']]
        children.custom_input(input_packet(children, parent, 4, kind='key', painted=restored['revision']))
        await control(children, 'wait', 'wait', child_id=child)
        history = session.children.supervisor.capture_source(child).messages
        assert 'CHILD-ANSWER' in str(history) and 'DONE' in str(history)
        assert not session.messages and session.permissions.mode == 'allow-all'
    finally:
        await close(session, nav, sources, children)


async def test_child_admitted_resize_revokes_old_frame_before_actual_worker_callback(tmp_path):
    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'custom')
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='custom-call',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
            arguments={}),)) if context.messages[-1].role == 'user' else
            AssistantMessage(content=(TextContent(text='DONE'),)))
    session.agent.set_stream_fn(adapter)
    try:
        children.negotiate({'child_custom_views': 1})
        await control(children, 'start', task='view', access='inherit')
        child = next(event['child_id'] for event in events if event['type'] == 'child_result')
        await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'child_extension_dialog')
        children.custom_input(input_packet(children, dialog, 1))
        await wait(lambda: children.custom_frames)
        first = children.custom_frames[dialog['dialog']['id']]
        # No yield between resize admission and stale-key attempt: this tests the
        # synchronous RPC admission guard, not eventual broker completion refusal.
        children.custom_input(input_packet(children, dialog, 2))
        assert dialog['dialog']['id'] not in children.custom_frames
        with pytest.raises(ValueError, match='current delivered frame'):
            children.custom_input(input_packet(children, dialog, 3, kind='key', painted=first['revision']))
        await wait(lambda: children.custom_frames)
        fresh = children.custom_frames[dialog['dialog']['id']]
        children.custom_input(input_packet(children, dialog, 4, kind='key', painted=fresh['revision']))
        await control(children, 'wait', 'wait', child_id=child)
        history = session.children.supervisor.capture_source(child).messages
        assert '"inputs": ["enter"]' in str(history) and not session.messages
    finally:
        await close(session, nav, sources, children)


async def test_child_superseded_frame_cannot_reopen_input_before_new_generation_returns(tmp_path, monkeypatch):
    import asyncio
    import threading

    session, _, nav, sources, children, events = setup(tmp_path, 'allow-all', 'custom')
    first_started, second_started = threading.Event(), threading.Event()
    release_first, release_second = threading.Event(), threading.Event()
    def adapter(model, context, options):
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='custom-call',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_custom_view')),
            arguments={}),)) if context.messages[-1].role == 'user' else
            AssistantMessage(content=(TextContent(text='DONE'),)))
    session.agent.set_stream_fn(adapter)
    try:
        children.negotiate({'child_custom_views': 1})
        await control(children, 'start', task='view', access='inherit')
        await wait(lambda: any(event['type'] == 'child_extension_dialog' for event in events))
        dialog = next(event for event in events if event['type'] == 'child_extension_dialog')
        identifier = dialog['dialog']['id']
        broker = session.children.extensions._dialogs[dialog['child_id']]
        view = broker.custom.views[identifier]
        render = view.render
        async def held(event):
            started = first_started if event['generation'] == 1 else second_started
            release = release_first if event['generation'] == 1 else release_second
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.003)
            return await render(event)
        monkeypatch.setattr(view, 'render', held)
        children.custom_input(input_packet(children, dialog, 1))
        await wait(first_started.is_set)
        newer = input_packet(children, dialog, 2)
        newer['event'].update(generation=2, width=20, height=3)
        children.custom_input(newer)
        release_first.set()
        await wait(second_started.is_set)
        await asyncio.sleep(0.02)
        assert identifier not in children.custom_frames
        assert not any(event['type'] == 'child_extension_custom_frame' for event in events)
        with pytest.raises(ValueError, match='current delivered frame'):
            children.custom_input(input_packet(children, dialog, 3, kind='key', painted=1))
        release_second.set()
        await wait(lambda: identifier in children.custom_frames)
        assert children.custom_frames[identifier]['generation'] == 2
        children.answer_dialog(packet(children, child_id=dialog['child_id'], id=identifier, value=None))
        await control(children, 'wait', 'wait', child_id=dialog['child_id'])
        assert not session.messages and session.permissions.mode == 'allow-all'
    finally:
        release_first.set()
        release_second.set()
        await close(session, nav, sources, children)
