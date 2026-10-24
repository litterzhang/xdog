from __future__ import annotations

import asyncio

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillRepo
from xdog.ai.types import AssistantMessage, DoneEvent, TextContent, ToolCall
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.core.slash_commands import execute_command
from xdog.coding.core.tools import get_default_tools


def create(tmp_path, mode="ask"):
    turns = []
    def stream(_model, context, _options):
        turns.append(context)
        last = context.messages[-1]
        if last.role == "user" and "write" in str(last.content):
            message = AssistantMessage(content=(ToolCall(id="write", name="filesystem", arguments={
                "action": "write", "path": "target", "content": "changed"}),), stop_reason="toolUse")
        else:
            message = AssistantMessage(content=(TextContent(text="done"),))
        async def generate():
            yield DoneEvent(message=message, stop_reason=message.stop_reason)
        future = asyncio.get_running_loop().create_future()
        future.set_result(message)
        return EventStream.from_async_generator(generate(), result_future=future)
    store = SessionManager(tmp_path / "sessions")
    session = AgentSession(agent=Agent(stream,
                                       skills=SkillRepo().register(directory=tmp_path / 'skills', name="shared"),
                                       config=AgentConfig(model="test/model"),
                                       tools=get_default_tools(tmp_path), tool_ctx={"fs_workspace": str(tmp_path)}),
                           session_data=store.create_session(model="test/model", working_dir=tmp_path),
                           session_manager=store, settings=SettingsManager(global_path=tmp_path / "settings.json"),
                           tool_repo=None, bash=None, working_dir=tmp_path, permissions=PermissionManager(mode))
    return session, turns


@pytest.mark.parametrize("mode", ["ask", "allow-all", "deny"])
async def test_host_read_only_blocks_actual_writes_and_explicit_release_keeps_policy(tmp_path, mode):
    session, contexts = create(tmp_path, mode)
    try:
        assert 'update_plan' not in {tool.name for tool in session.agent.state.tools}
        await execute_command("readonly", "on", session)
        await session.send_message("write")
        assert session.access.state.read_only and not (tmp_path / "target").exists()
        assert "READ ONLY" in str(contexts[0].system_prompt)
        assert session.session_manager.load_session(session.session_id).access['read_only'] is True
        await execute_command("readonly", "off", session)
        assert session.permissions.mode == mode and not session.access.state.read_only
        await session.send_message("write")
        assert (tmp_path / "target").exists() == (mode == "allow-all")
        branch = await asyncio.to_thread(session.create_branch)
        await execute_command("readonly", "on", session)
        # History navigation cannot release a live host read-only restriction.
        assert await asyncio.to_thread(session.restore_branch, branch)
        assert session.access.state.read_only
        await asyncio.to_thread(session.access_control, False)
        old = session.access
        await asyncio.to_thread(session.new_session)
        assert not session.access.state.read_only
        with pytest.raises(RuntimeError, match="closed"):
            old.set_read_only(False)
    finally:
        session.dispose()


async def test_legacy_planning_restore_retains_material_and_restricts_until_explicit_user_release(tmp_path):
    import json
    session, _ = create(tmp_path, 'allow-all')
    legacy = {'mode': 'planning', 'revision': 2, 'objective': 'Inspect only', 'steps': [
        {'description': 'Read files', 'status': 'pending'}]}
    try:
        target = session.session_manager.create_session(model='test/model', working_dir=tmp_path)
        path = session.session_manager._session_path(target.session_id)
        assert path.exists()
        raw = json.loads(path.read_text())
        raw.pop('access')
        raw['plan'] = legacy
        path.write_text(json.dumps(raw))
        assert session.switch_session(target.session_id)
        assert session.access.state.read_only
        assert session.session_data.legacy_plan == legacy
        await session.send_message('write')
        assert not (tmp_path / 'target').exists()
        await asyncio.to_thread(session.access_control, False)
        loaded = session.session_manager.load_session(target.session_id)
        assert not loaded.access['read_only'] and loaded.legacy_plan == legacy
        await session.send_message('write')
        assert (tmp_path / 'target').exists()
    finally:
        session.dispose()


def test_invalid_saved_scope_cannot_drop_current_read_only_state(tmp_path):
    import json
    session, _ = create(tmp_path)
    try:
        session.access_control(True)
        target = session.session_manager.create_session(working_dir=tmp_path)
        path = session.session_manager._session_path(target.session_id)
        raw = json.loads(path.read_text())
        raw['access'] = {'read_only': False, 'revision': True}
        path.write_text(json.dumps(raw))
        original = session.session_id
        before = path.read_bytes()
        with pytest.raises(ValueError, match='invalid|unavailable'):
            session.switch_session(target.session_id)
        assert session.session_id == original and path.read_bytes() == before
        assert session.access.state.read_only
    finally:
        session.dispose()


def test_saved_read_only_gate_restores_in_fresh_python_without_model_or_replay(tmp_path):
    import json
    import subprocess
    import sys
    import textwrap
    session, contexts = create(tmp_path, 'allow-all')
    session.access_control(True)
    sid = session.session_id
    session.dispose()
    source = textwrap.dedent('''
        import asyncio, json, sys
        from pathlib import Path
        from xdog.agent import Agent, AgentConfig, AgentContext, BeforeToolCallContext
        from xdog.ai.types import AssistantMessage, ToolCall
        from xdog.coding.core.agent_session import AgentSession
        from xdog.coding.core.permissions import PermissionManager
        from xdog.coding.core.session_manager import SessionManager
        from xdog.coding.core.settings_manager import SettingsManager
        from xdog.coding.core.tools import get_default_tools
        root, sid = Path(sys.argv[1]), sys.argv[2]
        def no_model(*args):
            raise AssertionError('Restore must not call a model')
        store = SessionManager(root / 'sessions')
        data = store.load_session(sid)
        session = AgentSession(agent=Agent(no_model, config=AgentConfig(model='test/model'),
            tools=get_default_tools(root)), session_data=data, session_manager=store,
            settings=SettingsManager(global_path=root / 'settings.json'), tool_repo=None,
            bash=None, working_dir=root, permissions=PermissionManager('allow-all'))
        try:
            args = {'action':'write', 'path':'target', 'content':'bad'}
            call = ToolCall(id='never-replay', name='filesystem', arguments=args)
            ctx = BeforeToolCallContext(assistant_message=AssistantMessage(), tool_call=call,
                args=args, context=AgentContext())
            block = asyncio.run(session._before_tool_call(ctx))
            assert block is not None and block.block
            assert not (root / 'target').exists()
            print(json.dumps(session.access.state.to_dict()))
        finally:
            session.dispose()
    ''')
    result = subprocess.run([sys.executable, '-c', source, str(tmp_path), sid],
                            text=True, capture_output=True, timeout=20, check=True)
    assert json.loads(result.stdout)['read_only'] is True and not contexts


@pytest.mark.parametrize('name', ['bash', 'mcp__server__read', 'ext_read', 'agent', 'agent_spawn', 'agent_control'])
async def test_hard_scope_denies_unknown_external_and_child_tools_before_extension_hook(tmp_path, name):
    from types import SimpleNamespace

    from xdog.agent import AgentContext, BeforeToolCallContext
    session, _ = create(tmp_path, 'allow-all')
    original = session.extensions
    calls = []
    async def event(*args):
        calls.append(args)
    try:
        session.access_control(True)
        session.extensions = SimpleNamespace(enabled=True, event=event)
        ctx = BeforeToolCallContext(assistant_message=AssistantMessage(), args={},
            tool_call=ToolCall(id='external', name=name, arguments={}), context=AgentContext())
        result = await session._before_tool_call(ctx)
        assert result is not None and result.block and not calls
    finally:
        session.extensions = original
        session.dispose()


async def test_read_rewritten_to_write_by_extension_is_rechecked(tmp_path):
    from types import SimpleNamespace

    from xdog.agent import AgentContext, BeforeToolCallContext
    session, _ = create(tmp_path, 'allow-all')
    original = session.extensions
    async def event(*args):
        pass
    async def pre(name, args):
        return {'action': 'write', 'path': 'target', 'content': 'unapproved'}
    try:
        session.access_control(True)
        session.extensions = SimpleNamespace(enabled=True, event=event, pre=pre)
        args = {'action': 'read', 'path': 'target'}
        ctx = BeforeToolCallContext(assistant_message=AssistantMessage(), args=args,
            tool_call=ToolCall(id='rewritten', name='filesystem', arguments=args), context=AgentContext())
        result = await session._before_tool_call(ctx)
        assert result is not None and result.block and not (tmp_path / 'target').exists()
    finally:
        session.extensions = original
        session.dispose()


async def test_scope_rechecked_after_pending_positive_approval(tmp_path):
    from xdog.agent import AgentContext, BeforeToolCallContext
    session, _ = create(tmp_path)
    requests = []
    session.permissions.set_request_handler(requests.append)
    args = {'action': 'write', 'path': 'target', 'content': 'forbidden'}
    ctx = BeforeToolCallContext(assistant_message=AssistantMessage(), args=args,
        tool_call=ToolCall(id='pending', name='filesystem', arguments=args), context=AgentContext())
    work = asyncio.create_task(session._before_tool_call(ctx))
    try:
        async with asyncio.timeout(3):
            while not requests:
                await asyncio.sleep(0)
        # Host revocation cannot be invalidated by an already queued UI reply.
        session.access.set_read_only(True)
        assert session.permissions.resolve(requests[0].id, 'allow_session')
        result = await asyncio.wait_for(work, 3)
        assert result is not None and result.block and not (tmp_path / 'target').exists()
    finally:
        session.dispose()
        await asyncio.gather(work, return_exceptions=True)
