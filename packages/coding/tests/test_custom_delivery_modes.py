"""Staged canonical custom steer/follow-up and idle continuation, never fake user input."""
from __future__ import annotations

import asyncio
import json

import pytest
from xdog.agent.core import CustomAgentMessage
from xdog.ai.types import AssistantMessage, TextContent, ToolCall, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation

from packages.coding.tests.test_rpc_extensions import close, session


async def wait(ui, predicate):
    async with asyncio.timeout(12):
        while True:
            if ui is not None:
                ui._poll()
            if predicate():
                return
            await asyncio.sleep(.003)


def approve(ui):
    panel = ui._extension_dialog_panel
    from xdog.tui.keys import KeyEvent
    panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    for _ in range(200):
        panel.render(80)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('trigger', [False, True])
async def test_native_idle_canonical_delivery_and_optional_existing_continuation(tmp_path, delivery, trigger):
    owner, _ = session(tmp_path, extra=('customdelivery',))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='CUSTOM-CONTINUED'),)))
    owner.agent.set_stream_fn(stream)
    ui = InteractiveMode(owner)
    try:
        ui._editor.set_text('UNCHANGED DRAFT')
        paths = tmp_path / 'actual-worker-imports.json'
        ui._handle_slash_command('ext-customdelivery-request', json.dumps(
            {'delivery': delivery, 'trigger': trigger, 'paths_file': str(paths)}))
        await wait(ui, lambda: ui._extension_dialog_panel is not None)
        assert not calls and not owner.agent.messages
        from pathlib import Path
        source_root = Path(__file__).resolve().parents[3]
        assert all(Path(value).is_relative_to(source_root) for value in json.loads(paths.read_text()))
        approve(ui)
        await wait(ui, lambda: ui._command is None and not ui._worker_active)
        assert len(calls) == int(trigger)
        assert sum(isinstance(row, CustomAgentMessage) for row in owner.agent.messages) == 1
        assert not any(isinstance(row, UserMessage) for row in owner.agent.messages)
        assert not (await owner.context_messages.capture())[1]
        if trigger:
            assert 'CANONICAL-CUSTOM-DELIVERY' in str(calls[0].messages)
            assert 'NEVER-MODEL-METADATA' not in str(calls[0].messages)
        assert ui._editor.get_text() == 'UNCHANGED DRAFT' and owner.permissions.mode == 'deny'
        saved = owner.session_manager.load_session(owner.session_id)
        assert sum(isinstance(row, CustomAgentMessage) for row in saved.messages) == 1
    finally:
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
async def test_rpc_trigger_waits_complete_command_receipt_and_continues_canonical_history(tmp_path, delivery):
    owner, _ = session(tmp_path, extra=('customdelivery',))
    calls, events = [], []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='RPC-CUSTOM-CONTINUED'),)))
    owner.agent.set_stream_fn(stream)
    entered, release = asyncio.Event(), asyncio.Event()
    async def send(event):
        entered.set()
        await release.wait()
        events.append(event)
    nav = RpcNavigation(owner, events.append)
    rpc = RpcExtensions(owner, events.append, navigation=nav, send=send)
    rpc.negotiate({'extension_context_messages': 1, 'extension_context_delivery_modes': 1})
    try:
        rpc.start({'request_id': 'command', 'session_id': owner.session_id, 'action': 'command',
                   'name': 'ext-customdelivery-request', 'args': json.dumps({'delivery': delivery, 'trigger': True})},
                  turn_running=False)
        await wait(None, lambda: any(event['type'] == 'extension_dialog' for event in events))
        event = next(event for event in events if event['type'] == 'extension_dialog')
        rpc.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                    'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await entered.wait()
        assert not owner.agent.messages and not calls
        release.set()
        await rpc.task
        await wait(None, lambda: rpc.prompt_task is not None)
        await rpc.prompt_task
        assert len(calls) == 1 and not any(isinstance(row, UserMessage) for row in owner.agent.messages)
        assert isinstance(owner.agent.messages[0], CustomAgentMessage)
        assert 'CANONICAL-CUSTOM-DELIVERY' in str(calls[0].messages)
    finally:
        release.set()
        await rpc.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
async def test_actual_native_tool_queues_custom_data_into_same_agent_loop(tmp_path, delivery):
    owner, _ = session(tmp_path, extra=('customdelivery',))
    owner.permissions.set_mode('allow-all')
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            tool = next(row for row in context.tools if row.name.startswith('ext_customdelivery_'))
            result = AssistantMessage(content=(ToolCall(id='tool', name=tool.name,
                arguments={'delivery': delivery, 'trigger': True}),))
        else:
            result = AssistantMessage(content=(TextContent(text='ACTIVE-DONE'),))
        return EventStream.empty(result)
    owner.agent.set_stream_fn(stream)
    ui = InteractiveMode(owner)
    try:
        ui._start_turn('ORIGINAL USER')
        await wait(ui, lambda: ui._extension_dialog_panel is not None)
        assert len(calls) == 1
        approve(ui)
        await wait(ui, lambda: not ui._worker_active)
        custom = [row for row in owner.agent.messages if isinstance(row, CustomAgentMessage)]
        assert len(custom) == 1 and custom[0].data['delivery'] == delivery
        assert len([row for row in owner.agent.messages if isinstance(row, UserMessage)]) == 1
        assert 'NEVER-MODEL-METADATA' not in str(calls)
        assert any('CANONICAL-CUSTOM-DELIVERY' in str(context.messages) for context in calls[1:])
        assert not owner.agent._custom_queue
    finally:
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('limit', ['text-only', 'media', 'bytes', 'allowed'])
async def test_new_idle_branch_validates_current_images_and_display_false_still_projects(tmp_path, delivery, limit):
    from dataclasses import asdict

    from xdog.ai.types import Model, VisionLimits

    from packages.agent.tests.test_context_messages import row
    from packages.agent.tests.test_extension_queued_input import IMAGE
    owner, calls = session(tmp_path)
    try:
        if limit != 'text-only':
            owner.model_info = Model(id='vision-model', input=('text', 'image'), vision_limits=VisionLimits(
                max_prompt_image_size=1 if limit == 'bytes' else 1024,
                supported_media_types=('image/jpeg',) if limit == 'media' else ('image/png',)))
        value = row(session_id=owner.session_id, delivery=delivery, display=False,
                    content=[{'type': 'text', 'text': 'HIDDEN UI VISIBLE MODEL'}, asdict(IMAGE)])
        if limit == 'allowed':
            await owner.context_messages.deliver(value, lambda: True, None)
            assert isinstance(owner.agent.messages[-1], CustomAgentMessage)
            await owner.send_message('', input_origin='custom_context')
            assert len(calls) == 1
            assert 'HIDDEN UI VISIBLE MODEL' in str(calls[0]) and 'NOT MODEL INPUT' not in str(calls[0])
        else:
            with pytest.raises(ValueError):
                await owner.context_messages.deliver(value, lambda: True, None)
            assert not calls and not owner.agent.messages and not owner.context_messages.uncertain
    finally:
        await close(owner)


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('failure', ['legacy', 'callback', 'transport', 'withdraw'])
async def test_raw_rpc_modes_need_capability_callback_and_delivered_original_result(tmp_path, delivery, failure):
    owner, calls = session(tmp_path, extra=('customdelivery',))
    events = []
    entered, release = asyncio.Event(), asyncio.Event()
    async def send(event):
        entered.set()
        if failure == 'withdraw':
            await release.wait()
        if failure == 'transport':
            raise OSError('Complete result transport failed')
        events.append(event)
    nav = RpcNavigation(owner, events.append)
    rpc = RpcExtensions(owner, events.append, navigation=nav, send=send)
    caps = {'extension_context_messages': 1}
    if failure != 'legacy':
        caps['extension_context_delivery_modes'] = 1
    rpc.negotiate(caps)
    try:
        rpc.start({'request_id': 'reject', 'session_id': owner.session_id, 'action': 'command',
            'name': 'ext-customdelivery-request', 'args': json.dumps(
                {'delivery': delivery, 'trigger': True, 'fail': failure == 'callback'})}, turn_running=False)
        if failure != 'legacy':
            await wait(None, lambda: any(event['type'] == 'extension_dialog' for event in events))
            event = next(event for event in events if event['type'] == 'extension_dialog')
            rpc.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                        'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        if failure == 'withdraw':
            await entered.wait()
            rpc.negotiate({'extension_context_delivery_modes': 0})
            rpc.negotiate({'extension_context_delivery_modes': 1})
            release.set()
        await asyncio.gather(rpc.task, return_exceptions=True)
        assert not owner.agent.messages and not calls and rpc.prompt_task is None
        assert not (await owner.context_messages.capture())[1]
        if failure == 'legacy':
            assert not any(event['type'] == 'extension_dialog' for event in events)
    finally:
        release.set()
        await rpc.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('reject', [False, True])
async def test_active_image_revalidated_at_canonical_checkpoint_after_model_metadata_changes(
    tmp_path, delivery, reject,
):
    from dataclasses import asdict

    from xdog.agent import AgentTool, AgentToolResult
    from xdog.ai.types import Model

    from packages.agent.tests.test_context_messages import row
    from packages.agent.tests.test_extension_queued_input import IMAGE
    owner, _ = session(tmp_path)
    calls = []
    owner.model_info = Model(id='vision', input=('text', 'image'))
    owner.permissions.set_mode('allow-all')
    async def execute(*args, **kwargs):
        await owner.context_messages.deliver(row(session_id=owner.session_id, delivery=delivery,
            display=False, content=[asdict(IMAGE)]), lambda: True, None)
        if reject:
            owner.model_info = Model(id='text', input=('text',))
        return AgentToolResult(content=(TextContent(text='ORIGINAL RESULT'),))
    tool = AgentTool(name='custom', label='custom', description='custom',
                     parameters={'type': 'object'}, execute=execute)
    def stream(model, context, options):
        calls.append(context)
        if len(calls) == 1:
            return EventStream.empty(AssistantMessage(content=(ToolCall(id='c', name='custom', arguments={}),)))
        if any('ImageContent' in str(item) for item in context.messages):
            saved = owner.session_manager.load_session(owner.session_id)
            assert any(isinstance(item, CustomAgentMessage) for item in saved.messages)
        return EventStream.empty(AssistantMessage(content=(TextContent(text='DONE'),)))
    owner.agent.set_tools([tool])
    owner.agent.set_stream_fn(stream)
    try:
        result = await owner.agent.prompt('ONLY USER')
        _ = [event async for event in result]
        assert sum(isinstance(item, CustomAgentMessage) for item in owner.agent.messages) == int(not reject)
        assert not owner.agent._custom_queue
        assert any('ImageContent' in str(call.messages) for call in calls) is (not reject)
        assert 'NOT MODEL INPUT' not in str(calls)
    finally:
        await close(owner)


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
async def test_idle_continuation_revalidates_image_after_profile_changes(tmp_path, delivery):
    from dataclasses import asdict

    from xdog.ai.types import Model

    from packages.agent.tests.test_context_messages import row
    from packages.agent.tests.test_extension_queued_input import IMAGE
    owner, calls = session(tmp_path)
    try:
        owner.model_info = Model(id='vision', input=('text', 'image'))
        await owner.context_messages.deliver(row(session_id=owner.session_id, delivery=delivery,
            content=[asdict(IMAGE)]), lambda: True, None)
        owner.model_info = Model(id='text', input=('text',))
        with pytest.raises(ValueError, match='image input'):
            await owner.send_message('', input_origin='custom_context')
        assert not calls
        assert sum(isinstance(item, CustomAgentMessage) for item in owner.agent.messages) == 1
    finally:
        await close(owner)


@pytest.fixture(autouse=True)
def custom_admission_uses_original_agent_loop(monkeypatch):
    from xdog.agent import Agent
    original = Agent.queue_custom_message
    def checked(agent, *args, **kwargs):
        assert asyncio.get_running_loop() is agent._cancel_loop
        return original(agent, *args, **kwargs)
    monkeypatch.setattr(Agent, 'queue_custom_message', checked)


async def test_custom_continuation_capability_cannot_be_canceled_by_unrelated_message_family(tmp_path):
    from xdog.ai.types import StartEvent
    owner, _ = session(tmp_path, extra=('customdelivery',))
    entered, stopped = asyncio.Event(), asyncio.Event()
    def stream(model, context, options):
        async def hold():
            try:
                entered.set()
                yield StartEvent(partial=AssistantMessage())
                await asyncio.Event().wait()
            finally:
                stopped.set()
        return EventStream(hold())
    owner.agent.set_stream_fn(stream)
    events = []
    nav = RpcNavigation(owner, events.append)
    rpc = RpcExtensions(owner, events.append, navigation=nav)
    rpc.negotiate({'extension_context_messages': 1, 'extension_context_delivery_modes': 1,
                   'extension_messages': 1, 'extension_message_images': 1})
    try:
        rpc.start({'request_id': 'owned', 'session_id': owner.session_id, 'action': 'command',
                   'name': 'ext-customdelivery-request', 'args': '{"trigger":true}'}, turn_running=False)
        await wait(None, lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        rpc.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                    'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await wait(None, entered.is_set)
        assert rpc.prompt_kind == 'custom_context'
        rpc.negotiate({'extension_messages': 0, 'extension_message_images': 0})
        await asyncio.sleep(.02)
        assert rpc.prompting and not stopped.is_set()
        rpc.negotiate({'extension_context_delivery_modes': 0})
        await asyncio.gather(rpc.prompt_task, return_exceptions=True)
        assert stopped.is_set() and not rpc.prompting
        assert not any(isinstance(row, UserMessage) for row in owner.messages)
    finally:
        await rpc.aclose()
        await nav.aclose()
        await close(owner)


async def test_combined_custom_trigger_and_reviewed_message_never_spawn_competing_producers(tmp_path):
    owner, calls = session(tmp_path, extra=('customdelivery',))
    events = []
    nav = RpcNavigation(owner, events.append)
    rpc = RpcExtensions(owner, events.append, navigation=nav)
    rpc.negotiate({'extension_context_messages': 1, 'extension_context_delivery_modes': 1, 'extension_messages': 1})
    try:
        rpc.start({'request_id': 'owned', 'session_id': owner.session_id, 'action': 'command',
                   'name': 'ext-customdelivery-request', 'args': '{"trigger":true,"also_message":true}'},
                  turn_running=False)
        for index in (1, 2):
            await wait(None, lambda: len([row for row in events if row['type'] == 'extension_dialog']) >= index)
            event = [row for row in events if row['type'] == 'extension_dialog'][index - 1]
            rpc.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                        'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await rpc.task
        await asyncio.sleep(0)
        if rpc.prompt_task is not None:
            await rpc.prompt_task
        assert len(calls) <= 1 and not rpc.prompting
        assert not any(row['type'] == 'extension_context_continuation' for row in events)
        assert any(row['type'] == 'extension_context_continuation_discarded' for row in events)
    finally:
        await rpc.aclose()
        await nav.aclose()
        await close(owner)
