"""Both hosts persist narrowed inventory before acknowledged lifecycle completion."""
from __future__ import annotations

import pytest

from packages.claw.tests.test_active_tool_persistence import close as close_claw
from packages.claw.tests.test_active_tool_persistence import create as create_claw
from packages.coding.tests.test_active_tool_persistence import close as close_coding
from packages.coding.tests.test_active_tool_persistence import create as create_coding
from packages.coding.tests.test_rpc_extensions import session as coding_extensions


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_acknowledged_unload_then_reload_and_restart_cannot_reenable_retired_selection(tmp_path, host):
    runtime = restored = None
    if host == 'coding':
        owner, calls = coding_extensions(tmp_path)
    else:
        runtime, calls = create_claw(tmp_path, extensions=('dialogue',))
        owner = runtime.get_or_create_session(allow_reset=False)
    def saved():
        if host == 'coding':
            return owner.session_manager.load_session(owner.session_id).active_tools
        return runtime.transcript_store.get_active_tools(owner.meta.session_id)
    async def select(names):
        if host == 'coding':
            owner.select_active_tools(names, expected_revision=owner.agent.tool_selection_revision)
        else:
            await owner.select_active_tools(names, expected_revision=owner.agent.tool_selection_revision)
    async def manage(action):
        if host == 'coding':
            await owner.extension_command('extensions', action + ' dialogue')
        else:
            await owner.extensions.manage(action, 'dialogue')
            await owner.initialize_extensions()
    try:
        await owner.initialize_extensions()
        selected = next(tool.name for tool in owner.agent.state.tools if tool.name.startswith('ext_dialogue_ask_'))
        await select([selected])
        await manage('unload')
        assert owner.agent.active_tool_selection == ()
        assert saved() == ()  # A successful lifecycle completion cannot leave a broader durable selection.
        await manage('reload')
        assert selected in {tool.name for tool in owner.agent.state.tools}
        assert owner.agent.active_tool_selection == () and saved() == ()
        if host == 'coding':
            data = owner.session_manager.load_session(owner.session_id)
            await close_coding(owner)
            restored, later_calls = await create_coding(tmp_path, data)
            await restored.initialize_extensions()
            assert restored.agent.active_tool_selection == ()
        else:
            await close_claw(runtime)
            restored, later_calls = create_claw(tmp_path, extensions=('dialogue',))
            next_owner = restored.get_or_create_session(allow_reset=False)
            await next_owner.initialize_extensions()
            assert next_owner.agent.active_tool_selection == ()
        assert not calls and not later_calls
    finally:
        if restored is not None:
            await (close_coding(restored) if host == 'coding' else close_claw(restored))
        await (close_coding(owner) if host == 'coding' else close_claw(runtime))


@pytest.mark.parametrize('host', ['coding', 'claw'])
@pytest.mark.parametrize('ending', ['apply', 'cancel', 'failure'])
async def test_retirement_disk_failure_and_cancellation_settle_without_regrant(tmp_path, monkeypatch, host, ending):
    import asyncio
    import threading
    runtime = None
    if host == 'coding':
        owner, calls = coding_extensions(tmp_path)
    else:
        runtime, calls = create_claw(tmp_path, extensions=('dialogue',))
        owner = runtime.get_or_create_session(allow_reset=False)
    entered, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()
    work = None
    try:
        await owner.initialize_extensions()
        name = next(tool.name for tool in owner.agent.state.tools if tool.name.startswith('ext_dialogue_ask_'))
        if host == 'coding':
            owner.select_active_tools([name], expected_revision=owner.agent.tool_selection_revision)
            store, method = owner.session_manager, 'save_session'
        else:
            await owner.select_active_tools([name], expected_revision=owner.agent.tool_selection_revision)
            store, method = runtime.transcript_store, 'save_active_tools'
        original = getattr(store, method)
        def blocked(*args, **kwargs):
            assert threading.get_ident() != main_thread, 'Retirement write blocked host event loop'
            entered.set()
            assert release.wait(8), 'Test retirement gate was not released'
            if ending == 'failure':
                raise OSError('Injected retirement storage failure')
            return original(*args, **kwargs)
        monkeypatch.setattr(store, method, blocked)
        async def retire():
            if host == 'coding':
                await owner.extension_command('extensions', 'unload dialogue')
            else:
                await owner.extensions.manage('unload', 'dialogue')
                await owner.initialize_extensions()
        work = asyncio.create_task(retire())
        async with asyncio.timeout(8):
            while not entered.is_set():
                await asyncio.sleep(.003)
        assert not work.done() and owner._tool_selection_retiring
        with pytest.raises(RuntimeError, match='persistence'):
            owner._children_idle()
        assert owner.agent.active_tool_selection == ()
        if ending == 'cancel':
            work.cancel()
        await asyncio.sleep(.02)
        assert not work.done() and owner._tool_selection_retiring
        release.set()
        outcome = (await asyncio.gather(work, return_exceptions=True))[0]
        assert isinstance(outcome, asyncio.CancelledError) if ending == 'cancel' else (
            isinstance(outcome, OSError) if ending == 'failure' else outcome is None)
        assert owner.agent.active_tool_selection == () and not owner._tool_selection_retiring
        def saved():
            return (store.load_session(owner.session_id).active_tools if host == 'coding' else
                    store.get_active_tools(owner.meta.session_id))
        assert saved() == ((name,) if ending == 'failure' else ())
        monkeypatch.setattr(store, method, original)
        await owner.initialize_extensions()  # Retry narrowing before any later model/command may proceed.
        assert saved() == () and not calls
    finally:
        release.set()
        if work is not None:
            await asyncio.gather(work, return_exceptions=True)
        await (close_coding(owner) if host == 'coding' else close_claw(runtime))


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_actual_tool_callback_retirement_is_persisted_before_next_model_step(tmp_path, host):
    from xdog.ai.types import AssistantMessage, TextContent, ToolCall
    from xdog.ai.utils.event_stream import EventStream
    runtime = None
    if host == 'coding':
        owner, _ = coding_extensions(tmp_path, extra=('toolretirement',))
        from xdog.coding.core.permissions import PermissionManager
        owner.permissions = PermissionManager('allow-all')
    else:
        runtime, _ = create_claw(tmp_path, extensions=('toolretirement',))
        runtime.extension_allow_tools = ('toolretirement',)
        owner = runtime.get_or_create_session(allow_reset=False)
    contexts = []
    try:
        await owner.initialize_extensions()
        selected = next(tool.name for tool in owner.agent.state.tools if tool.name.startswith('ext_toolretirement_'))
        if host == 'coding':
            owner.select_active_tools([selected], expected_revision=owner.agent.tool_selection_revision)
        else:
            await owner.select_active_tools([selected], expected_revision=owner.agent.tool_selection_revision)
        def saved():
            return (owner.session_manager.load_session(owner.session_id).active_tools if host == 'coding' else
                    runtime.transcript_store.get_active_tools(owner.meta.session_id))
        def stream(model, context, options):
            contexts.append(context)
            if len(contexts) == 1:
                call = ToolCall(id='retire', name=selected, arguments={})
                return EventStream.empty(AssistantMessage(content=(call,)))
            assert saved() == ()
            assert not context.tools
            return EventStream.empty(AssistantMessage(content=(TextContent(text='retirement done'),)))
        from xdog.agent.extensions.providers import ExtensionProviderStream
        owner.agent.set_stream_fn(ExtensionProviderStream(owner.extensions, stream))
        if host == 'coding':
            await owner.send_message('retire tool')
        else:
            from xdog.claw.core.types import UserInput
            result = await owner.run_turn(UserInput(group_id='main', content='retire tool'))
            assert result.error is None
        assert len(contexts) == 2 and saved() == ()
        assert owner.agent.active_tool_selection == ()
    finally:
        await (close_coding(owner) if host == 'coding' else close_claw(runtime))


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_prepared_navigation_commits_only_available_selected_names(tmp_path, host):
    import asyncio

    from xdog.ai.types import AssistantMessage, UserMessage
    runtime = None
    if host == 'coding':
        owner, calls = await create_coding(tmp_path)
    else:
        runtime, calls = create_claw(tmp_path)
        owner = runtime.get_or_create_session(allow_reset=False)
    try:
        await owner.initialize_extensions()
        if host == 'coding':
            target = owner.session_manager.create_session(model='offline/model', working_dir=tmp_path)
            target.active_tools = ('missing', 'current_time')
            target.messages = [UserMessage(content='target'), AssistantMessage()]
            owner.session_manager.save_session(target)
            assert await asyncio.to_thread(owner.switch_session, target.session_id)
            assert owner.agent.active_tool_selection == ('current_time',)
            assert owner.session_manager.load_session(owner.session_id).active_tools == ('current_time',)
        else:
            from xdog.claw.core.persistence.transcript_convert import messages_to_transcript
            store = runtime.transcript_store
            original = owner.meta.session_id
            target = store.create_session('main')
            store.replace_transcript(target.session_id, messages_to_transcript([UserMessage(content='target')]))
            store.save_active_tools(target.session_id, ('missing', 'current_time'))
            store.activate_session('main', original)
            await asyncio.to_thread(runtime.navigate_session, 'resume', target.session_id, '', -1, original)
            assert runtime._active_session.agent.active_tool_selection == ('current_time',)
            assert store.get_active_tools(target.session_id) == ('current_time',)
        assert not calls
    finally:
        await (close_coding(owner) if host == 'coding' else close_claw(runtime))


@pytest.mark.parametrize('host', ['coding', 'claw'])
async def test_real_mcp_disconnect_retirement_survives_reconnect_without_tool_calls(tmp_path, host):
    import json
    import sys
    from pathlib import Path

    from xdog.agent.mcp import McpManager
    runtime = None
    if host == 'coding':
        owner, calls = await create_coding(tmp_path)
        owner.mcp.close()
    else:
        runtime, calls = create_claw(tmp_path)
        owner = runtime.get_or_create_session(allow_reset=False)
        runtime.mcp.close()
    log = tmp_path / 'requests'
    fixture = Path(__file__).parents[2] / 'agent/tests/fixtures/mcp_server.py'
    manager = McpManager({'fixture': {'command': sys.executable, 'args': [str(fixture), 'modern', str(log)]}}, tmp_path)
    if host == 'coding':
        owner.mcp = manager
    else:
        runtime.mcp = manager
    async def manage(action):
        await (owner.manage_mcp(action, 'fixture') if host == 'coding' else runtime.manage_mcp(action, 'fixture'))
    try:
        await owner.initialize_extensions()
        await manage('connect')
        selected = manager.tools()[0].name
        if host == 'coding':
            owner.select_active_tools([selected], expected_revision=owner.agent.tool_selection_revision)
        else:
            await owner.select_active_tools([selected], expected_revision=owner.agent.tool_selection_revision)
        await manage('disconnect')
        assert owner.agent.active_tool_selection == ()
        saved = (owner.session_manager.load_session(owner.session_id).active_tools if host == 'coding' else
                 runtime.transcript_store.get_active_tools(owner.meta.session_id))
        assert saved == ()
        await manage('connect')
        assert selected in {tool.name for tool in owner.agent.state.tools}
        assert owner.agent.active_tool_selection == () and not calls
        assert not any(json.loads(line).get('method') == 'tools/call' for line in log.read_text().splitlines())
    finally:
        await manager.aclose()
        await (close_coding(owner) if host == 'coding' else close_claw(runtime))
