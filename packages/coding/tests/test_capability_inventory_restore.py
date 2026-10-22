"""Extension-model SDK bootstrap cannot retire unpublished application choices."""
from __future__ import annotations

import asyncio
import json
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from xdog.coding.config import get_sessions_dir
from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
from xdog.coding.core.session_manager import SessionManager

from packages.coding.tests.test_extension_provider_startup import ALIAS, Base


@pytest.mark.parametrize('saved', [('filesystem', 'eyes', 'flow'), ('filesystem', 'flow'), (), None])
async def test_real_sdk_extension_bootstrap_preserves_saved_selection_until_full_publication(
        tmp_path, monkeypatch, saved):
    home = tmp_path / 'coding'
    home.mkdir()
    extensions = Path(__file__).resolve().parents[2] / 'agent/tests/fixtures/extensions'
    settings = home / 'settings.json'
    settings.write_text(json.dumps({'default_model': ALIAS, 'extensions': ['providermock'],
                                    'extension_dir': str(extensions)}))
    monkeypatch.setenv('CODING_DIR', str(home))
    entries = tuple(entry for entry in entry_points(group='xdog.capabilities') if entry.name in {'flow', 'eyes'})
    assert {entry.name for entry in entries} == {'flow', 'eyes'}
    monkeypatch.setattr('xdog.agent.capabilities.runtime.entry_points', lambda **_: entries)
    base = Base()
    monkeypatch.setattr('xdog.ai.load', lambda: base)
    store = SessionManager(get_sessions_dir())
    data = store.create_session(model=ALIAS, working_dir=tmp_path)
    data.active_tools = saved
    store.save_session(data)
    session = await asyncio.to_thread(lambda: create_agent_session(CreateSessionOptions(
        working_dir=tmp_path, config_path=settings, resume_id=data.session_id)).session)
    try:
        assert session.model == ALIAS and base.calls == 0
        assert session.application_capabilities.get('flow') is None
        assert store.load_session(data.session_id).active_tools == saved
        if saved is not None:
            assert session._tool_selection_restore_pending
            assert session.agent.active_tool_selection == ()
        await session.send_message('use the original extension model')
        assert not session._tool_selection_restore_pending
        assert session.agent.active_tool_selection == saved
        assert store.load_session(data.session_id).active_tools == saved
        assert {'flow', 'eyes'} <= {tool.name for tool in session.agent.state.tools}
        assert not {'flow', 'eyes'} & set(session.agent.active_skills)
        assert base.calls == 0  # No primary-provider fallback.
        # Republishing does not restore a user-disabled capability or activate bodies.
        await session.initialize_capabilities()
        await session.initialize_extensions()
        assert session.agent.active_tool_selection == saved
        assert store.load_session(data.session_id).active_tools == saved
    finally:
        await session.aclose()


async def test_failed_application_start_keeps_saved_choice_and_startup_mask(tmp_path, monkeypatch):
    from xdog.coding.core.agent_session import AgentSession

    # A real session owner, with application startup failure before publication.
    from packages.coding.tests.test_child_agents import create
    session, contexts = create(tmp_path)
    saved = ('filesystem', 'eyes', 'flow')
    session.session_data.active_tools = saved
    session._tool_selection_restore_pending = True
    session.agent.set_active_tools([], expected_revision=session.agent.tool_selection_revision)
    session._persist()
    async def fail(_host):
        raise RuntimeError('application unavailable')
    monkeypatch.setattr(session.application_capabilities, 'start', fail)
    try:
        await AgentSession.initialize_extensions(session)
        with pytest.raises(RuntimeError, match='application unavailable'):
            await session.send_message('must not call model')
        assert not contexts and session._tool_selection_restore_pending
        assert session.agent.active_tool_selection == ()
        assert session.session_manager.load_session(session.session_id).active_tools == saved
    finally:
        await session.aclose()
