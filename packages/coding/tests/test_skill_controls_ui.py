from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.skills import SkillManager
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


def write_skills(directory):
    for slug, scope, marker in [('guard', 'session', 'SESSION-GUARD-BODY'), ('once', 'turn', 'TURN-ONLY-BODY')]:
        skill = directory / slug
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(f'---\nname: {slug}\ndescription: {slug} description\n'
                                      f'metadata:\n  scope: {scope}\n---\n{marker}\n')


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_actual_skill_controls_owned_prefix_turn_context_expiration_no_auto_submit(tmp_path, kind, monkeypatch):
    calls = []
    def stream(model, context, options):
        calls.append(context)
        return EventStream.empty(AssistantMessage(model=model, content=(TextContent(text='SKILL-TURN-DONE'),)))
    server = task = writer = None
    if kind == 'coding':
        directory = tmp_path / 'skills'
        write_skills(directory)
        manager = SkillManager(shared_dir=directory, packaged={})
        monkeypatch.setattr('xdog.coding.core.slash_commands.skill_manager', lambda: manager)
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model'), skills=manager),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_registry=None, bash=None, permissions=PermissionManager('deny'))
        ui = InteractiveMode(session)
        async def control(action, slug=''):
            if action == 'load':
                session.activate_skill(slug)
            elif action == 'unload':
                session.deactivate_skill(slug)
    else:
        directory = tmp_path / 'data' / 'skills'
        write_skills(directory)
        config = ClawConfig(model='test/model', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'skills.sock'), pid_file=str(tmp_path / 'skills.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('test/model', stream)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path)
        task = asyncio.create_task(ui._run_connection(reader, writer))
        async def control(action, slug=''):
            ui._skills_command(action + (' ' + slug if slug else ''))
            await wait(lambda: not ui._skills_request)
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            assert ui._skills_supported
        ui._editor.set_text('retained skill draft')
        await control('load', 'guard')
        await control('load', 'once')
        if kind == 'claw':
            session = server._orchestrator._runtimes['main']._active_session
            assert session._store.get_active_skills(session.meta.session_id) == ('guard', 'once')
            restored = type(session)(runtime=session._runtime, session_meta=session.meta)
            try:
                assert restored._active_skills == frozenset({'guard', 'once'})
                assert 'SESSION-GUARD-BODY' in str(restored.agent.state.system_prompt)
                assert not restored.agent.state.messages
            finally:
                restored.dispose()
            with pytest.raises(ValueError, match='owner changed'):
                await session.skills_control('unload', 'guard', lambda: False)
            assert session._active_skills == frozenset({'guard', 'once'})
            session.agent._update_state(is_streaming=True)
            try:
                with pytest.raises(ValueError, match='idle'):
                    await session.skills_control('unload', 'guard')
            finally:
                session.agent._update_state(is_streaming=False)
        assert not calls and not session.agent.state.messages
        assert ui._editor.get_text() == 'retained skill draft'
        ui._handle_submit('run selected skills')
        await wait(lambda: len(calls) == 1
                   and (not ui._worker_active if kind == 'coding' else ui._active_run_id is None))
        assert 'SESSION-GUARD-BODY' in str(calls[0].system_prompt)
        assert 'TURN-ONLY-BODY' not in str(calls[0].system_prompt)
        assert str(calls[0].messages).count('TURN-ONLY-BODY') == 1
        assert 'TURN-ONLY-BODY' not in str(session.agent.state.messages)
        assert 'SESSION-GUARD-BODY' in str(session.agent.state.system_prompt)
        ui._handle_submit('next turn')
        await wait(lambda: len(calls) == 2
                   and (not ui._worker_active if kind == 'coding' else ui._active_run_id is None))
        assert 'TURN-ONLY-BODY' not in str(calls[1].messages)
        await control('unload', 'guard')
        assert 'SESSION-GUARD-BODY' not in str(session.agent.state.system_prompt)
        assert len(calls) == 2
        await control('load', 'once')
        async def fail_initialize():
            raise RuntimeError('offline preparation failure')
        with monkeypatch.context() as failed:
            failed.setattr(session, 'initialize_extensions', fail_initialize)
            if kind == 'coding':
                with pytest.raises(RuntimeError, match='offline preparation'):
                    await session.send_message('failed attempted turn')
                assert session.active_skills == frozenset({'once'})
            else:
                from xdog.claw.core.types import UserInput
                result = await session.run_turn(UserInput(group_id='main', content='failed attempted turn'))
                assert result.error == 'offline preparation failure'
                assert session._active_skills == frozenset({'once'})
        assert len(calls) == 2
        ui._handle_submit('retry selected skill after preparation failure')
        await wait(lambda: len(calls) == 3
                   and (not ui._worker_active if kind == 'coding' else ui._active_run_id is None))
        assert str(calls[2].messages).count('TURN-ONLY-BODY') == 1
        assert session.agent.active_skills == ()
        if kind == 'coding':
            assert session.permissions.mode == 'deny'
        else:
            assert session._store.get_active_skills(session.meta.session_id) == ()
            started, released = threading.Event(), threading.Event()
            original = session._store.save_active_skills
            def delayed_save(identifier, selected):
                started.set()
                assert released.wait(5)
                original(identifier, selected)
            monkeypatch.setattr(session._store, 'save_active_skills', delayed_save)
            pending = asyncio.create_task(session.skills_control('load', 'guard'))
            assert await asyncio.to_thread(started.wait, 5)
            pending.cancel()
            await asyncio.sleep(0)
            pending.cancel()
            released.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert session._active_skills == frozenset({'guard'})
            assert session._store.get_active_skills(session.meta.session_id) == ('guard',)
            assert 'SESSION-GUARD-BODY' in str(session.agent.state.system_prompt)
            ui._skills_request = 'malformed-action'
            ui._queue_request({'type': 'skills_control', 'request_id': ui._skills_request,
                'group_id': 'main', 'session_id': session.meta.session_id, 'action': []})
            await wait(lambda: not ui._skills_request)
            assert session._active_skills == frozenset({'guard'})
            ui._queue_request({'type': 'ping', 'skill_controls': 0})
            await wait(lambda: not ui._skills_supported)
            ui._queue_request({'type': 'ping', 'skill_controls': 1})
            await wait(lambda: ui._skills_supported)
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        if kind == 'coding':
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


@pytest.mark.parametrize('scenario', ['stale', 'malformed', 'withdrawn', 'disconnected', 'unsolicited', 'timeout'])
def test_skill_receipts_cannot_change_foreign_or_unavailable_owner(scenario, monkeypatch):
    ui = ChatApp('/tmp/not-connected-skill-test.sock')
    ui._connection_ready = scenario != 'disconnected'
    ui._skills_supported = scenario != 'withdrawn'
    ui._gateway_session_id = 'current'
    ui._skills_session = 'current'
    ui._skills_request = '' if scenario == 'unsolicited' else 'request'
    ui._skills_deadline = 0
    if scenario == 'timeout':
        ui._poll()
        assert not ui._skills_request
    ui._handle_response({'type': 'skills_result', 'group_id': ui.group_id,
        'request_id': 'request', 'session_id': 'old' if scenario == 'stale' else 'current',
        'success': True, 'active': [None] if scenario == 'malformed' else ['guard'],
        'skills': [{'slug': 'guard', 'scope': 'session', 'active': True, 'description': 'RECEIPT-MARKER'}]})
    assert ui._gateway_session_id == 'current'
    assert 'RECEIPT-MARKER' not in str(ui._chat_log.render(120))
    ui._request_exit()
