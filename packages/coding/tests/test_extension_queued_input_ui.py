"""Real native producer/tool callback review queues into the same Agent, not another loop."""
from __future__ import annotations

import asyncio
import base64

import pytest
from xdog.ai.types import AssistantMessage, ImageContent, Model, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.coding.tests.test_rpc_extensions import close, session
from packages.coding.tests.test_rpc_images import PNG


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'error', 'image'])
async def test_actual_native_tool_queues_only_reviewed_original_run_after_success(tmp_path, delivery, ending):
    owner, _ = session(tmp_path, extra=('queuedhost',))
    owner.permissions = PermissionManager('allow-all')  # Explicit fixture policy, never set by review.
    owner.model_info = Model(id='fixture', input=('text', 'image'))
    calls = []
    def stream(model, context, options):
        calls.append(context)
        result = (AssistantMessage(content=(ToolCall(id='request', name=next(tool.name for tool in context.tools
                if tool.name.startswith('ext_queuedhost_request')),
            arguments={'delivery': delivery, 'ending': ending, 'image': ending == 'image'}),))
            if len(calls) == 1 else AssistantMessage(content=(TextContent(text='QUEUE-REPLY'),)))
        return EventStream.empty(result)
    owner.agent.set_stream_fn(stream)
    ui = InteractiveMode(owner)
    original_queue = owner.agent.queue_input
    def owned_queue(*args, **kwargs):
        assert asyncio.get_running_loop() is ui._worker_loop_ref
        return original_queue(*args, **kwargs)
    owner.agent.queue_input = owned_queue
    async def wait(predicate):
        async with asyncio.timeout(12):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        ui._editor.set_text('PRIVATE UNSENT QUEUE DRAFT')
        image = attachment(base64.b64decode(PNG), 'private.png')
        ui._composer.items[image.id] = image
        ui._start_turn('Original model turn')
        await wait(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'queue_input' and len(calls) == 1
        assert panel.dialog['images'] == [] and PNG not in str(panel.dialog)
        assert bool(panel.dialog['image_summaries']) is (ending == 'image')
        if ending != 'deny':
            panel.handle_input(KeyEvent('right'))
        panel.handle_input(KeyEvent('enter'))
        for _ in range(160):
            panel.render(80)
            if panel.form._visible:
                break
            panel.handle_input(KeyEvent('pagedown'))
        assert panel.form._visible
        panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
        assert len(calls) == 1
        panel.handle_input(KeyEvent('enter'))
        await wait(lambda: not ui._worker_active)
        queued = [message for message in owner.messages if message.role == 'user'
                  and 'REVIEWED-QUEUE' in str(message.content)]
        if ending in {'apply', 'image'}:
            assert len(queued) == 1
            parts = queued[0].content
            text = parts if isinstance(parts, str) else parts[0].text
            assert text == '[User-approved message requested by extension queuedhost]\n\nREVIEWED-QUEUE /quit'
            assert sum(isinstance(part, ImageContent) for part in parts) == (1 if ending == 'image' else 0)
        else:
            assert not queued
        assert owner.permissions.mode == 'allow-all' and not ui._exit_requested
        assert ui._editor.get_text() == 'PRIVATE UNSENT QUEUE DRAFT' and ui._composer.items[image.id] is image
        assert not owner.agent.pending_inputs() and not ui._routed_inputs
    finally:
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('delivery', ['steer', 'follow_up'])
@pytest.mark.parametrize('approve', [False, True])
async def test_actual_child_queue_review_keeps_parent_messages_draft_and_tool_policy_separate(
    tmp_path, delivery, approve,
):
    from xdog.agent.supervision_config import ChildConfig
    owner, calls = session(tmp_path, extra=('queuedhost',))
    owner.permissions.set_mode('allow-all')
    ui = InteractiveMode(owner)
    async def pump(predicate):
        async with asyncio.timeout(12):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        ui._editor.set_text('PARENT PRIVATE CHILD-QUEUE DRAFT')
        image = attachment(base64.b64decode(PNG), 'parent-only.png')
        ui._composer.items[image.id] = image
        await owner.initialize_extensions()
        ui._poll()
        child = await owner.children.supervisor.start('QUEUE-START IMAGE ' + delivery.upper(), access='inherit',
            config=ChildConfig(model='ext-queuedhost-offline/queue', max_model_calls=6).to_dict())
        await pump(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'queue_input' and panel.dialog['child_id'] == child
        assert panel.dialog['images'] == [] and len(panel.dialog['image_summaries']) == 1
        if approve:
            panel.handle_input(KeyEvent('right'))
        panel.handle_input(KeyEvent('enter'))
        for _ in range(160):
            panel.render(80)
            if panel.form._visible:
                break
            panel.handle_input(KeyEvent('pagedown'))
        assert panel.form._visible
        panel.handle_input(KeyEvent('enter'))
        await pump(lambda: not owner.children.supervisor.active)
        record = await owner.children.supervisor.control('wait', child)
        assert record['status'] == 'completed'
        transcript = owner.children.supervisor._children[child].transcript
        queued = [row for row in transcript if row.role == 'user' and 'REVIEWED-QUEUE' in str(row.content)]
        assert bool(queued) is approve
        if approve:
            assert len(queued) == 1 and sum(isinstance(part, ImageContent) for part in queued[0].content) == 1
        assert not owner.messages and not calls and not ui._routed_inputs
        assert ui._editor.get_text() == 'PARENT PRIVATE CHILD-QUEUE DRAFT' and ui._composer.items[image.id] is image
        assert owner.permissions.mode == 'allow-all'
    finally:
        ui._request_exit()
        await close(owner)


async def test_native_command_pending_status_is_live_boolean_without_a_dialog_or_draft_read(tmp_path):
    owner, calls = session(tmp_path, extra=('queuedhost',))
    ui = InteractiveMode(owner)
    try:
        ui._editor.set_text('PRIVATE STATUS DRAFT')
        ui._handle_slash_command('ext-queuedhost-pending', '')
        async with asyncio.timeout(8):
            while ui._command is not None:
                ui._poll()
                assert ui._extension_dialog_panel is None
                await asyncio.sleep(.003)
        assert 'PENDING-MESSAGES false' in str(ui._chat_log.render(160))
        assert ui._editor.get_text() == 'PRIVATE STATUS DRAFT' and not calls and not owner.messages
    finally:
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('mode', ['deny', 'ask'])
async def test_tool_permission_precedes_separate_queued_input_consent_and_never_changes_policy(tmp_path, mode):
    owner, _ = session(tmp_path, extra=('queuedhost',))
    owner.permissions.set_mode(mode)
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(content=(ToolCall(id='queue-permission',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_queuedhost_request')),
            arguments={}),)) if len(calls) == 1 else AssistantMessage(content=(TextContent(text='DONE'),)))
    owner.agent.set_stream_fn(stream)
    ui = InteractiveMode(owner)
    async def wait(predicate):
        async with asyncio.timeout(10):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        ui._start_turn('Original user model turn')
        if mode == 'ask':
            await wait(lambda: ui._permission_prompt is not None)
            assert ui._extension_dialog_panel is None
            permission = ui._permission_prompt
            permission.set_render_budget(20)
            permission.render(100)
            permission.handle_input(KeyEvent('enter'))
            await wait(lambda: ui._extension_dialog_panel is not None)
            assert ui._permission_prompt is None
            ui._extension_dialog_panel.handle_input(KeyEvent('escape'))  # Tool consent is not queue consent.
        await wait(lambda: not ui._worker_active)
        assert len(calls) == 2 and owner.permissions.mode == mode
        assert not any(row.role == 'user' and 'REVIEWED-QUEUE' in str(row.content) for row in owner.messages)
        assert not owner.agent.pending_inputs() and not ui._routed_inputs
    finally:
        ui._request_exit()
        await close(owner)
