"""Durable names through the actual command owner; no history/permission/provider mutation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, TextContent, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionData, SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


def app(tmp_path: Path):
    def stream(*args):
        pytest.fail('Session naming cannot make model calls')
        return EventStream.empty(AssistantMessage())
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='offline/model')),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('deny'))
    session.agent.replace_messages([UserMessage(content='ORIGINAL-QUESTION'),
        AssistantMessage(content=(TextContent(text='ORIGINAL-ANSWER'),))])
    session._persist()
    return InteractiveMode(session), session, manager


async def wait(ui, predicate):
    async with asyncio.timeout(5):
        while not predicate():
            ui._poll()
            await asyncio.sleep(0.003)


async def test_actual_name_commands_browser_search_restart_and_plan_entry_persistence_keep_same_owner(tmp_path):
    ui, session, manager = app(tmp_path)
    state, data = session.agent.state, session.session_data
    history = json.loads(manager._session_path(session.session_id).read_text())["messages"]
    plan, extensions, children = session.plan, session.extensions, session.children
    try:
        ui._editor.set_text('RETAINED-NAME-DRAFT')
        ui._handle_slash_command('name', '开发 release review')
        await wait(ui, lambda: ui._command is None)
        saved = manager.load_session(session.session_id)
        assert saved.name == '开发 release review' and saved.summary == 'ORIGINAL-ANSWER'
        assert saved.to_dict()["messages"] == history and session.agent.state is state
        assert session.session_data is data and session.plan is plan and session.extensions is extensions
        assert session.children is children and session.permissions.mode == 'deny'
        assert ui._editor.get_text() == 'RETAINED-NAME-DRAFT'
        ui._open_navigation('sessions')
        ui._navigation_provider.request('sessions', 'release review')
        await wait(ui, lambda: not ui._navigation_provider.snapshot().busy)
        rows = ui._navigation_provider.snapshot().entries
        assert len(rows) == 1 and rows[0].title == '开发 release review' and rows[0].active
        ui._close_navigation()
        # Captured plan/entry persistence owners must not overwrite the newly saved name.
        session.plan.change('start', session.plan.state.revision, 'next plan')
        session.extensions.entries.append('named', [{'kind': 'note', 'data': {'named': True}}])
        assert manager.load_session(session.session_id).name == '开发 release review'
        assert SessionManager(manager._dir).get_most_recent().name == '开发 release review'
        branch = session.create_branch(at_index=1)
        assert session.restore_branch(branch)
        assert manager.load_session(session.session_id).name == '开发 release review'
        ui._handle_slash_command('name', '')
        await wait(ui, lambda: ui._command is None)
        assert 'Session name: 开发 release review' in str(ui._chat_log.render(120))
        ui._handle_slash_command('name', '-')
        await wait(ui, lambda: ui._command is None)
        assert not manager.load_session(session.session_id).name and session.session_data.summary == 'ORIGINAL-ANSWER'
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()


async def test_name_save_failure_invalid_input_and_busy_agent_preserve_bytes_and_metadata(tmp_path, monkeypatch):
    ui, session, manager = app(tmp_path)
    before = manager._session_path(session.session_id).read_bytes()
    updated = session.session_data.updated_at
    try:
        original = manager._write
        def failed(value):
            raise OSError('name save failed')
        monkeypatch.setattr(manager, '_write', failed)
        ui._handle_slash_command('name', 'Uncommitted')
        await wait(ui, lambda: ui._command is None)
        assert 'name save failed' in str(ui._chat_log.render(120))
        assert session.session_data.name == '' and session.session_data.updated_at == updated
        assert manager._session_path(session.session_id).read_bytes() == before
        monkeypatch.setattr(manager, '_write', original)
        with pytest.raises(ValueError):
            session.rename_session('unsafe\x1b')
        ui._worker_active = True
        ui._handle_slash_command('name', 'Busy')
        assert ui._editor.get_text() == '/name Busy' and ui._command is None
        ui._worker_active = False
        assert manager._session_path(session.session_id).read_bytes() == before
        legacy = SessionData.from_dict({'session_id': 'legacy'})
        assert legacy.name == ''
    finally:
        ui._worker_active = False
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
        session.dispose()
