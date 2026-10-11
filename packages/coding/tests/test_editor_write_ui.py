"""Coding native full-review proposal/CAS, distinct from original command editor reads."""
from __future__ import annotations

import asyncio

import pytest
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent, KeyEventType

from packages.coding.tests.test_rpc_extensions import close, session


async def pump(ui, predicate):
    async with asyncio.timeout(10):
        while True:
            ui._poll()
            if predicate():
                return
            await asyncio.sleep(.003)


def approve(panel, *, allow=True):
    if allow:
        panel.handle_input(KeyEvent('right'))
    panel.handle_input(KeyEvent('enter'))  # Unpainted must not confirm.
    for _ in range(160):
        panel.render(45)
        if panel.form._visible:
            break
        panel.handle_input(KeyEvent('pagedown'))
    assert panel.form._visible
    panel.handle_input(KeyEvent('enter', event_type=KeyEventType.REPEAT))
    panel.handle_input(KeyEvent('enter'))


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'text', 'cursor', 'cancel'])
async def test_actual_native_editor_proposal_full_review_callback_success_and_private_cas(tmp_path, ending):
    owner, calls = session(tmp_path, extra=('editorwrite',))
    ui = InteractiveMode(owner)
    try:
        ui._editor.set_text('ORIGINAL UNSENT')
        ui._handle_slash_command('ext-editorwrite-write', 'fail' if ending == 'fail' else '')
        await pump(ui, lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'propose'
        assert panel.dialog['editor_write']['session_id'] == owner.session_id
        assert 'ORIGINAL UNSENT' not in str(panel.dialog) and not calls
        if ending == 'text':
            ui._editor.set_text('CHANGED BY USER')
        elif ending == 'cursor':
            ui._editor._cursor = 0
        if ending == 'cancel':
            panel.handle_input(KeyEvent('escape'))
        else:
            approve(panel, allow=ending != 'deny')
        await pump(ui, lambda: ui._command is None)
        assert ui._editor.get_text() == ('PROPOSED 界\nunsent' if ending == 'apply' else
                                         'CHANGED BY USER' if ending == 'text' else 'ORIGINAL UNSENT')
        assert not owner.messages and not calls and owner.permissions.mode == 'deny'
        assert not ui._editor_write_reviews._pending
    finally:
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('scope', ['tool', 'event', 'pre', 'post', 'input', 'context'])
async def test_native_noncommand_original_owner_proposal_keeps_old_editor_api_denied(tmp_path, scope):
    owner, calls = session(tmp_path, extra=('editorwrite',))
    owner.extensions.settings['editorwrite'] = {'scope': scope}
    ui = InteractiveMode(owner)
    task = None
    try:
        ui._editor.set_text('PRIVATE PARENT DRAFT')
        ui._bind_extension_dialogs()
        await owner.extensions.initialize()
        from xdog.ai.types import UserMessage
        runtime = owner.extensions
        tool = next(t for t in runtime.tools() if 'editorwrite' in t.name)
        operation = {'tool': lambda: tool.execute('x', {}),
            'event': lambda: runtime.event('turn_start'), 'pre': lambda: runtime.pre('safe', {}),
            'post': lambda: runtime.post('safe', {}, 'original'),
            'input': lambda: runtime.input(UserMessage(content='original')),
            'context': lambda: runtime.transform([])}[scope]
        task = asyncio.create_task(operation())
        await pump(ui, lambda: ui._extension_dialog_panel is not None)
        assert 'PRIVATE PARENT DRAFT' not in str(ui._extension_dialog_panel.dialog)
        approve(ui._extension_dialog_panel)
        await pump(ui, task.done)
        await task
        await pump(ui, lambda: not ui._editor_write_reviews._pending)
        assert ui._editor.get_text() == 'PROPOSED 界\nunsent' and not calls and not owner.messages
    finally:
        ui._request_exit()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await close(owner)


@pytest.mark.parametrize('ending', ['apply', 'text', 'sibling-form', 'withdraw', 'no-form'])
async def test_actual_child_native_lease_never_falls_back_to_parent_or_sibling_composer(tmp_path, ending):
    owner, calls = session(tmp_path, extra=('editorwrite',))
    marker, release = tmp_path / 'reviewed', tmp_path / 'release'
    owner.extensions.settings['editorwrite'] = {'marker': str(marker), 'release': str(release)}
    from xdog.ai.types import AssistantMessage, StartEvent
    from xdog.ai.utils.event_stream import EventStream
    def hold(*args):
        calls.append(args)
        async def generate():
            yield StartEvent(partial=AssistantMessage())
            await asyncio.Event().wait()
        return EventStream(generate())
    owner.agent.set_stream_fn(hold)
    ui = InteractiveMode(owner)
    task = None
    try:
        ui._bind_extension_dialogs()
        ui._poll()  # Capture the actual child UI token before child creation.
        child = await owner.children.supervisor.start('hold child')
        await pump(ui, lambda: len(calls) == 1)
        ui._editor.set_text('PARENT NEVER CHANGES')
        panel = None
        if ending != 'no-form':
            ui._open_children()
            panel = ui._child_panel
            panel._selected_id = child
            panel._form('steer')
            panel._input.set_text('CHILD ORIGINAL')
            panel.render(80)
        runtime = owner.children.extensions.owners[child]
        tool = next(t for t in runtime.tools() if 'editorwrite' in t.name)
        task = asyncio.create_task(tool.execute('child-proposal', {}))
        if ending == 'no-form':
            await pump(ui, marker.exists)
            assert marker.read_text() == 'False'
        else:
            await pump(ui, lambda: ui._extension_dialog_panel is not None)
            assert ui._extension_dialog_panel.dialog['child_id'] == child
            approve(ui._extension_dialog_panel)
            await pump(ui, marker.exists)
            assert marker.read_text() == 'True'
            assert panel._input.get_text() == 'CHILD ORIGINAL'
            if ending == 'text':
                panel._input.set_text('CHILD USER CHANGE')
            elif ending == 'sibling-form':
                panel._selected_id = 'different-child'
                panel._form('steer')
            elif ending == 'withdraw':
                owner.children.extensions.enable_editor_write(ui._child_ui_lease, False)
                owner.children.extensions.enable_editor_write(ui._child_ui_lease, True)
        release.write_text('finish original callback cleanup')
        await pump(ui, task.done)
        await task
        # Commit/discard travels through the actual original child publish binding.
        await pump(ui, lambda: not ui._editor_write_reviews._pending)
        if panel is not None:
            assert (panel._input.get_text() == 'PROPOSED 界\nunsent') == (ending == 'apply')
        assert ui._editor.get_text() == 'PARENT NEVER CHANGES' and len(calls) == 1
        assert not owner.messages and not owner.children.supervisor._children[child].agent.pending_inputs()
    finally:
        release.write_text('settle')
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('mode', ['deny', 'ask', 'allow-all'])
async def test_actual_coding_model_tool_permission_gate_precedes_editor_review(tmp_path, mode):
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    owner, calls = session(tmp_path, extra=('editorwrite',))
    owner.permissions.set_mode(mode)
    def stream(model, context, options):
        calls.append(context)
        if context.messages[-1].role == 'user':
            name = next(tool.name for tool in context.tools if 'editorwrite' in tool.name)
            message = AssistantMessage(content=(ToolCall(id='proposal-tool', name=name, arguments={}),),
                                       stop_reason='toolUse')
        else:
            message = AssistantMessage(content=(TextContent(text='CANONICAL TOOL FINISHED'),))
        return EventStream.empty(message)
    owner.agent.set_stream_fn(stream)
    ui = InteractiveMode(owner)
    try:
        ui._handle_submit('explicit initial user input')
        if mode == 'ask':
            await pump(ui, lambda: ui._permission_prompt is not None)
            assert ui._extension_dialog_panel is None
            # Original tool approval is independent from later editor review.
            permission = ui._permission_prompt
            permission.set_render_budget(20)
            permission.render(100)
            permission.handle_input(KeyEvent('enter'))
            await pump(ui, lambda: ui._permission_prompt is None)
        if mode != 'deny':
            await pump(ui, lambda: ui._extension_dialog_panel is not None)
            ui._editor.set_text('UNSENT DURING TOOL REVIEW')
            # Changing the draft after proposal admission must reject this CAS.
            approve(ui._extension_dialog_panel)
        await pump(ui, lambda: not ui._worker_active)
        assert len(calls) == 2 and owner.permissions.mode == mode
        assert ui._editor.get_text() == ('' if mode == 'deny' else 'UNSENT DURING TOOL REVIEW')
        assert not ui._extension_dialog_panel and not ui._editor_write_reviews._pending
        assert all('PROPOSED' not in str(message.content) for message in owner.messages)
    finally:
        ui._request_exit()
        await close(owner)
