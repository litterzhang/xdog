"""Original native/RPC producers must finish before reviewed durable active-tool CAS."""
from __future__ import annotations

import asyncio

import pytest
from xdog.ai.types import AssistantMessage, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.coding.modes.rpc.extensions import RpcExtensions
from xdog.coding.modes.rpc.navigation import RpcNavigation
from xdog.coding.modes.rpc.rpc_mode import _handle_prompt
from xdog.tui.keys import KeyEvent

from packages.coding.tests.test_rpc_compaction import wait
from packages.coding.tests.test_rpc_extensions import close, session


def install(owner, ending):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        message = (AssistantMessage(content=(ToolCall(id='select',
            name=next(tool.name for tool in context.tools if tool.name.startswith('ext_aftertools_select')),
            arguments={'names': [], 'fail': ending == 'fail', 'error': ending == 'error'}),))
            if len(calls) == 1 else AssistantMessage(content=(TextContent(text='PARENT FINISHED'),)))
        return EventStream.empty(message)
    owner.permissions.set_mode('allow-all')
    owner.agent.set_stream_fn(stream)
    return calls


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'error'])
async def test_native_active_selection_waits_actual_thread_exit_and_preserves_permissions(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('aftertools',))
    marker, release = tmp_path / 'reviewed', tmp_path / 'release'
    owner.extensions.settings['aftertools'] = {'marker': str(marker), 'release': str(release)}
    calls = install(owner, ending)
    ui = InteractiveMode(owner)
    async def pump(predicate):
        async with asyncio.timeout(12):
            while True:
                ui._poll()
                if predicate():
                    return
                await asyncio.sleep(.003)
    try:
        ui._editor.set_text('UNSENT TOOL SELECTION DRAFT')
        ui._start_turn('Original user turn')
        await pump(lambda: ui._extension_dialog_panel is not None)
        panel = ui._extension_dialog_panel
        assert panel.dialog['operation'] == 'select_tools' and panel.dialog['after_run'] is True
        before = tuple(tool.name for tool in owner.agent.active_tools)
        if ending != 'deny':
            panel.handle_input(KeyEvent('right'))
        panel.handle_input(KeyEvent('enter'))
        for _ in range(160):
            panel.render(100)
            if panel.form._visible:
                break
            panel.handle_input(KeyEvent('pagedown'))
        assert panel.form._visible
        panel.handle_input(KeyEvent('enter'))
        await pump(marker.exists)
        assert tuple(tool.name for tool in owner.agent.active_tools) == before and len(calls) == 1
        release.write_text('Allow original callback cleanup')
        await pump(lambda: not ui._worker_active and ui._worker_thread is not None
                   and not ui._worker_thread.is_alive() and ui._command is None
                   and (ending != 'apply' or not owner.agent.active_tools))
        if ending == 'apply':
            assert owner.agent.active_tool_selection == ()
            assert owner.session_manager.load_session(owner.session_id).active_tools == ()
        else:
            assert owner.agent.active_tool_selection is None
        assert owner.permissions.mode == 'allow-all' and ui._editor.get_text() == 'UNSENT TOOL SELECTION DRAFT'
        # Selection was not changed for the still-running producer.
        assert len(calls) == (1 if ending == 'fail' else 2) and calls[-1].tools
    finally:
        release.write_text('Settle')
        ui._request_exit()
        await close(owner)


@pytest.mark.parametrize('ending', ['apply', 'deny', 'fail', 'error', 'withdraw', 'old-client'])
async def test_rpc_after_run_tools_is_independent_and_waits_prompt_result_settlement(tmp_path, ending):
    owner, _ = session(tmp_path, extra=('aftertools',))
    marker, release = tmp_path / 'reviewed', tmp_path / 'release'
    owner.extensions.settings['aftertools'] = {'marker': str(marker), 'release': str(release)}
    calls = install(owner, ending)
    events = []
    nav = RpcNavigation(owner, events.append)
    host = RpcExtensions(owner, events.append, navigation=nav)
    host.negotiate({'extension_active_tools_after_run': int(ending != 'old-client')})
    assert not host.tools_enabled and not host.after_run_compaction.enabled
    async def run():
        async with host.bind('original'):
            await _handle_prompt(owner, 'Original user turn', events.append)
    task = asyncio.create_task(run())
    try:
        if ending != 'old-client':
            await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
            event = next(row for row in events if row['type'] == 'extension_dialog')
            response = {'id': event['dialog']['id'], 'request_id': event['request_id'],
                        'session_id': event['session_id'], 'scope': event['scope'], 'value': ending != 'deny'}
            with pytest.raises(ValueError):
                host.answer({**response, 'scope': 'foreign'})
            host.answer(response)
        await wait(marker.exists)
        assert owner.agent.active_tool_selection is None and len(calls) == 1
        if ending == 'withdraw':
            host.negotiate({'extension_active_tools_after_run': 0})
            host.negotiate({'extension_active_tools_after_run': 1})
        release.write_text('Settle original callback')
        async with asyncio.timeout(10):
            await task
        await asyncio.sleep(0)
        async with asyncio.timeout(10):
            while host.after_run_tools.busy or host.after_run_compaction.jobs:
                await asyncio.sleep(.003)
        assert owner.agent.active_tool_selection == (() if ending == 'apply' else None)
        if ending == 'apply':
            result = next(row for row in events if row['type'] == 'extension_active_tools_after_run_result')
            assert result['applied']
            ended = next(row for row in events if row['type'] == 'message_end')
            assert events.index(ended) < events.index(result)
            assert owner.session_manager.load_session(owner.session_id).active_tools == ()
        assert len(calls) == (1 if ending == 'fail' else 2) and calls[-1].tools
        assert owner.permissions.mode == 'allow-all'
    finally:
        release.write_text('Settle')
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await host.aclose()
        await nav.aclose()
        await close(owner)


@pytest.mark.parametrize('boundary', ['before', 'after'])
async def test_rpc_selection_cancel_joins_disk_owner_and_does_not_deny_an_admitted_commit(
    tmp_path, monkeypatch, boundary,
):
    import threading
    owner, _ = session(tmp_path, extra=('aftertools',))
    install(owner, 'apply')
    entered, release = threading.Event(), threading.Event()
    original = owner.select_active_tools
    def held(*args, **kwargs):
        if boundary == 'after':
            original(*args, **kwargs)
        entered.set()
        assert release.wait(8), 'Test release must settle the owned writer'
        if boundary == 'before':
            original(*args, **kwargs)
    monkeypatch.setattr(owner, 'select_active_tools', held)
    events = []
    nav = RpcNavigation(owner, events.append)
    host = RpcExtensions(owner, events.append, navigation=nav)
    host.negotiate({'extension_active_tools_after_run': 1})
    async def run():
        async with host.bind('original'):
            await _handle_prompt(owner, 'Original user turn', events.append)
    task = asyncio.create_task(run())
    try:
        await wait(lambda: any(row['type'] == 'extension_dialog' for row in events))
        event = next(row for row in events if row['type'] == 'extension_dialog')
        host.answer({'id': event['dialog']['id'], 'request_id': event['request_id'],
                     'session_id': event['session_id'], 'scope': event['scope'], 'value': True})
        await wait(entered.is_set)
        host.after_run_tools.abort()
        host.negotiate({'extension_active_tools_after_run': 0})
        await asyncio.sleep(.01)
        assert host.after_run_tools.busy  # Cancellation cannot abandon the original disk writer.
        release.set()
        async with asyncio.timeout(10):
            while host.after_run_tools.busy:
                await asyncio.sleep(.003)
        result = next(row for row in events if row['type'] == 'extension_active_tools_after_run_result')
        assert result['applied'] is (boundary == 'after')
        assert owner.agent.active_tool_selection == (() if boundary == 'after' else None)
        if boundary == 'after':
            assert not result['effects_uncertain']
        else:
            assert result['effects_uncertain']  # Never pretend every failed receipt proves a rollback.
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await host.aclose()
        await nav.aclose()
        await close(owner)
