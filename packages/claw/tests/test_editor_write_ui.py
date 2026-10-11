"""Claw original callback/editor ownership: reviewed replacement never reads or sends a draft."""
from __future__ import annotations

import asyncio
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.tui.attachments import attachment
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.claw.tests.test_extension_message_ui import PNG, native_ui


@asynccontextmanager
async def editor_server(tmp_path, *, scope='command', held=False, stream=None):
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions/editorwrite', root / 'editorwrite')
    calls = []
    def default_stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user' and scope in {'tool', 'pre', 'post'}:
            name = (next(tool.name for tool in context.tools if tool.name.startswith('ext_editorwrite_'))
                    if scope == 'tool' else 'current_time')
            result = AssistantMessage(content=(ToolCall(id='proposal-tool', name=name, arguments={}),),
                                      stop_reason='toolUse')
        else:
            result = AssistantMessage(content=(TextContent(text='EDITOR-MODEL-DONE'),))
        return EventStream.empty(result)
    marker, release = tmp_path / 'reviewed', tmp_path / 'release'
    config = ClawConfig(model='offline/model', data_dir=str(tmp_path / 'data'),
        socket_path=str(tmp_path / 'socket'), pid_file=str(tmp_path / 'pid'),
        groups=(GroupDef(id='main', is_main=True, workspace=str(tmp_path)),),
        extensions=('editorwrite',), extension_dir=str(root), extension_allow_tools=('editorwrite',),
        extension_settings={'editorwrite': {'scope': scope,
            **({'marker': str(marker), 'release': str(release)} if held else {})}})
    server = GatewayServer(config)
    with patch('xdog.claw.core.runtime.gateway._build_model_and_options',
               return_value=('offline/model', stream or default_stream)):
        await server.start()
    runtime = server._orchestrator._runtimes['main']
    owner = runtime.get_or_create_session(allow_reset=False)
    try:
        yield server, runtime, owner, config, calls, marker, release
    finally:
        release.write_text('cleanup')
        await asyncio.wait_for(server.stop(), 10)


def approve(panel, allow=True):
    assert panel.dialog['operation'] == 'propose'
    if allow:
        panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))
    for _ in range(160):
        panel.render(60)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'cancel', 'text', 'cursor', 'images',
                                     'withdraw', 'source', 'old-client', 'foreign'])
async def test_parent_command_review_callback_cleanup_and_original_private_cas(tmp_path, ending):
    async with editor_server(tmp_path, held=True) as (_, runtime, owner, config, calls, marker, release):
        async with native_ui(config) as (ui, wait):
            assert ui._extension_editor_write_supported
            generation = ui._extension_editor_write_generation
            if ending == 'old-client':
                ui._queue_request({'type': 'ping', 'extension_editor_write': 0})
                await wait(lambda: not ui._extension_editor_write_supported)
            ui._editor.set_text('PRIVATE UNSENT ORIGINAL')
            ui._editor._cursor = 4
            image = attachment(PNG, 'private.png')
            ui._composer.items[image.id] = image
            ui._extension_command('ext-editorwrite-write', 'fail' if ending == 'fail' else '')
            try:
                if ending != 'old-client':
                    await wait(lambda: ui._extension_dialog_panel is not None)
                    panel = ui._extension_dialog_panel
                    assert panel.dialog['editor_write']['session_id'] == owner.meta.session_id
                    assert panel.dialog['editor_write']['method'] == 'xdog/command'
                    assert panel.dialog['editor_write_generation'] == generation
                    assert 'PRIVATE UNSENT' not in str(panel.dialog) and image.id not in str(panel.dialog)
                    if ending == 'foreign':
                        from packages.claw.tests.test_extension_message_gateway import peer
                        async with peer(config) as (send, receive, _writer):
                            await send({'type': 'extension_dialog_answer', 'id': panel.dialog['id'],
                                        'value': {'approved': True, 'revision': 'a' * 64}})
                            assert (await receive('extension_dialog_ack'))['accepted'] is False
                        assert not marker.exists()
                    if ending == 'cancel':
                        panel.handle_input(KeyEvent('escape'))
                    else:
                        approve(panel, ending != 'deny')
                await wait(marker.exists)
                assert ui._extension_pending is not None and not calls and not owner.agent.messages
                assert ui._editor.get_text() == 'PRIVATE UNSENT ORIGINAL' and ui._editor._cursor == 4
                if ending == 'text':
                    ui._editor.set_text('CHANGED PRIVATE DRAFT')
                elif ending == 'cursor':
                    ui._editor._cursor = 2
                elif ending == 'images':
                    ui._composer.items.clear()
                elif ending == 'withdraw':
                    ui._queue_request({'type': 'ping', 'extension_editor_write': 0})
                    await wait(lambda: not ui._extension_editor_write_supported)
                    ui._queue_request({'type': 'ping', 'extension_editor_write': 1})
                    await wait(lambda: ui._extension_editor_write_supported
                               and ui._extension_editor_write_generation != generation)
                elif ending == 'source':
                    runtime._source_revision += 1
                release.write_text('settle callback finally')
                await wait(lambda: ui._extension_pending is None)
                assert ui._editor.get_text() == ('PROPOSED 界\nunsent' if ending in {'apply', 'foreign'} else
                    'CHANGED PRIVATE DRAFT' if ending == 'text' else 'PRIVATE UNSENT ORIGINAL')
                assert len(ui._composer.items) == (0 if ending == 'images' else 1)
                assert not calls and not owner.agent.messages and ui._active_run_id is None
                assert not ui._editor_write_reviews._pending
                assert owner.extensions.editor_write_handler is None
            finally:
                release.write_text('finish')


@pytest.mark.parametrize('scope', ['tool', 'event', 'pre', 'post', 'input', 'context'])
async def test_canonical_turn_callback_proposal_keeps_old_composer_reads_denied(tmp_path, scope):
    async with editor_server(tmp_path, scope=scope) as (_, _, owner, config, calls, _marker, _release):
        async with native_ui(config) as (ui, wait):
            ui._editor.set_text('PRIVATE UNSENT DURING TURN')
            ui._start_message('original admitted model input', ())
            await wait(lambda: ui._extension_dialog_panel is not None)
            assert 'PRIVATE UNSENT' not in str(ui._extension_dialog_panel.dialog)
            approve(ui._extension_dialog_panel)
            await wait(lambda: ui._active_run_id is None)
            assert ui._editor.get_text() == 'PROPOSED 界\nunsent'
            assert len(calls) == (2 if scope in {'tool', 'pre', 'post'} else 1)
            assert 'PROPOSED' not in str(owner.agent.messages) and 'PRIVATE UNSENT' not in str(owner.agent.messages)
            assert not owner.extensions._failed and owner.extensions.editor_write_handler is None
            assert not ui._editor_write_reviews._pending


@pytest.mark.parametrize('ending', ['apply', 'deny', 'text', 'cursor', 'images', 'sibling-form', 'withdraw', 'no-form'])
async def test_actual_child_model_tool_reviews_only_its_selected_original_draft(tmp_path, ending):
    import threading

    from xdog.ai.types import DoneEvent, StartEvent
    entered, model_release = threading.Event(), threading.Event()
    calls = []
    def stream(model, context, options):
        calls.append(context)
        if len(calls) != 1:
            return EventStream.empty(AssistantMessage(content=(TextContent(text='CHILD-DONE'),)))
        tool = next(tool.name for tool in context.tools if tool.name.startswith('ext_editorwrite_'))
        message = AssistantMessage(content=(ToolCall(id='child-proposal', name=tool, arguments={}),),
                                   stop_reason='toolUse')
        async def generate():
            entered.set()
            yield StartEvent(partial=AssistantMessage())
            while not model_release.is_set():
                await asyncio.sleep(.003)
            yield DoneEvent(message=message)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    async with editor_server(tmp_path, scope='child', held=True, stream=stream) as (
            _server, _runtime, owner, config, _calls, marker, release):
        async with native_ui(config) as (ui, wait):
            ui._editor.set_text('PARENT PRIVATE UNTOUCHED')
            ui._editor._cursor = 3
            ui._agents_command('start --inherit child proposal')
            await wait(lambda: entered.is_set() and not ui._children_pending)
            child = next(iter(owner.children.extensions.owners))
            child_agent = owner.children.supervisor._children[child].agent
            panel, image = None, attachment(PNG, 'child-private.png')
            if ending != 'no-form':
                ui._open_children()
                await wait(lambda: not ui._child_panel._busy and bool(ui._child_panel._entries))
                panel = ui._child_panel
                panel._selected_id = child
                panel._form('steer')
                panel._input.set_text('CHILD PRIVATE ORIGINAL')
                panel._input._cursor = 6
                panel.composer.items[image.id] = image
                panel.render(80)
            try:
                model_release.set()
                if ending != 'no-form':
                    await wait(lambda: ui._extension_dialog_panel is not None)
                    dialog = ui._extension_dialog_panel
                    assert dialog.dialog['child_id'] == child
                    assert dialog.dialog['editor_write']['child_id'] == child
                    assert dialog.dialog['editor_write']['method'] == 'tools/call'
                    assert 'PRIVATE' not in str(dialog.dialog)
                    approve(dialog, ending != 'deny')
                await wait(marker.exists)
                assert marker.read_text() == str(ending not in {'no-form', 'deny'})
                assert ui._editor.get_text() == 'PARENT PRIVATE UNTOUCHED'
                if panel is not None:
                    assert panel._input.get_text() == 'CHILD PRIVATE ORIGINAL'
                    if ending == 'text':
                        panel._input.set_text('CHILD USER CHANGE')
                    elif ending == 'cursor':
                        panel._input._cursor = 2
                    elif ending == 'images':
                        panel.composer.items.clear()
                    elif ending == 'sibling-form':
                        panel._selected_id = 'different-child'
                        panel._form('steer')
                    elif ending == 'withdraw':
                        ui._queue_request({'type': 'ping', 'extension_editor_write': 0})
                        await wait(lambda: not ui._extension_editor_write_supported)
                        ui._queue_request({'type': 'ping', 'extension_editor_write': 1})
                        await wait(lambda: ui._extension_editor_write_supported)
                release.write_text('complete child callback finally')
                await wait(lambda: len(calls) == 2 and not owner.children.supervisor.active)
                await wait(lambda: not ui._editor_write_reviews._pending)
                if panel is not None:
                    assert (panel._input.get_text() == 'PROPOSED 界\nunsent') is (ending == 'apply')
                    if ending not in {'images', 'sibling-form'}:
                        assert panel.composer.items == {image.id: image}
                assert ui._editor.get_text() == 'PARENT PRIVATE UNTOUCHED' and ui._editor._cursor == 3
                assert not owner.agent.messages
                assert not child_agent.pending_inputs() and 'PROPOSED' not in str(child_agent.messages)
            finally:
                model_release.set()
                release.write_text('cleanup child')
