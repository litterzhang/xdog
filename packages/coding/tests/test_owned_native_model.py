"""Same-adapter native catalog, canonical selection, complete capabilities and failure preservation."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, Model, TextContent, UserMessage, VisionLimits
from xdog.ai.utils.event_stream import EventStream
from xdog.claw.channels.tui.tui_app import ChatApp
from xdog.claw.config import ClawConfig, GroupDef
from xdog.claw.core.runtime.gateway import GatewayServer
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode


class OwnedAdapter:
    def __init__(self):
        self.calls = []
        self.available = True
        self.profiles = (Model(id='owned/first', name='First', context_window=32000, max_prompt_tokens=24000,
            max_tokens=1000, reasoning=True, supported_efforts=('low', 'high'), input=('text', 'image'),
            vision_limits=VisionLimits(max_prompt_images=1)),
            Model(id='owned/second', name='Second', context_window=16000, max_prompt_tokens=8000, max_tokens=500,
                  reasoning=False, supports_tool_calls=False))
    def models(self):
        return self.profiles if self.available else ()
    def resolve_model(self, name):
        if not self.available:
            return None
        aliases = {'first': 'owned/first', 'second': 'owned/second'}
        return next((model for model in self.profiles if model.id == aliases.get(name, name)), None)
    def __call__(self, model, context, options):
        self.calls.append((model, context, options))
        return EventStream.empty(AssistantMessage(content=(TextContent(text='OWNED-NATIVE-ANSWER'),)))


@pytest.mark.parametrize('kind', ['coding', 'claw'])
async def test_both_native_ui_catalog_canonical_id_caps_unknown_denial_no_global_fallback(tmp_path, kind, monkeypatch):
    adapter = OwnedAdapter()
    server = task = writer = None
    if kind == 'coding':
        store = SessionManager(tmp_path / 'sessions')
        session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='owned/first', context_window=32000)),
            session_data=store.create_session(working_dir=tmp_path), session_manager=store,
            settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
            tool_repo=None, bash=None, permissions=PermissionManager('deny'), model_info=adapter.profiles[0])
        session.set_model('owned/first')
        session.set_thinking_level('high')
        ui = InteractiveMode(session)
    else:
        config = ClawConfig(model='owned/first', data_dir=str(tmp_path / 'data'),
            socket_path=str(tmp_path / 'owned.sock'), pid_file=str(tmp_path / 'owned.pid'),
            groups=(GroupDef(id='main', is_main=True),))
        server = GatewayServer(config)
        with patch('xdog.claw.core.runtime.gateway._build_model_and_options', return_value=('owned/first', adapter)):
            await server.start()
        ui = ChatApp(config.socket_path)
        reader, writer = await asyncio.open_unix_connection(config.socket_path, limit=4 * 1024 * 1024)
        task = asyncio.create_task(ui._run_connection(reader, writer))
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    async def model_command(value):
        if kind == 'coding':
            ui._handle_slash_command('model', value)
            await wait(lambda: ui._command is None)
        else:
            ui._handle_submit('/model' + (' ' + value if value else ''))
            await wait(lambda: ui._extension_model_pending is None)
    try:
        if kind == 'claw':
            await wait(lambda: ui._connection_ready)
            await model_command('')
            session = server._orchestrator._runtimes['main']._active_session
            await server._orchestrator.reasoning('main', session.meta.session_id, 'high')
        monkeypatch.setattr('xdog.ai.load', lambda: (_ for _ in ()).throw(AssertionError('GLOBAL-FALLBACK-FORBIDDEN')))
        ui._editor.set_text('RETAINED-NATIVE-DRAFT')
        await model_command('')
        assert 'owned/first' in str(ui._chat_log.render(140)) and 'owned/second' in str(ui._chat_log.render(140))
        await model_command('second')
        assert session.agent.state.model == 'owned/second' and session.agent.context_window == 16000
        assert session.agent.max_prompt_tokens == 8000 and session.agent.supports_tool_calls is False
        assert session.agent.options.thinking is None
        assert ui._editor.get_text() == 'RETAINED-NATIVE-DRAFT' and not adapter.calls
        state = session.agent.state
        await model_command('foreign/unavailable')
        assert session.agent.state == state and not adapter.calls
        await model_command('first')
        assert session.agent.state.model == 'owned/first' and session.agent.context_window == 32000
        assert session.agent.max_prompt_tokens == 24000 and session.agent.supports_tool_calls is True
        if kind == 'coding':
            assert session.model_info.vision_limits.max_prompt_images == 1 and session.permissions.mode == 'deny'
            saved = session.session_manager.load_session(session.session_id)
            assert saved.model == 'owned/first' and saved.settings['model'] == 'owned/first'
        else:
            assert session._store.get_model(session.meta.session_id) == 'owned/first'
    finally:
        ui._request_exit()
        if 'session' in locals():
            await session.children.runtime.aclose()
            await session.extensions.aclose()
        if kind == 'coding':
            session.dispose()
        else:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            writer.close()
            await writer.wait_closed()
            await server.stop()


def test_coding_selection_save_and_unavailable_saved_navigation_leave_owner_untouched(tmp_path, monkeypatch):
    adapter = OwnedAdapter()
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='owned/first')),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_repo=None, bash=None, model_info=adapter.profiles[0])
    try:
        session.set_model('owned/first')
        session.agent.replace_messages([UserMessage(content='PRESERVED-CONTEXT'), AssistantMessage()])
        session._persist()
        before, options = session.agent.state, session.agent.options
        data, extensions = session.session_data, session.extensions
        with monkeypatch.context() as failing:
            failing.setattr(manager, 'save_session', lambda *args: (_ for _ in ()).throw(OSError('offline save')))
            with pytest.raises(OSError):
                session.set_model('second')
        assert session.agent.state == before and session.agent.options == options and session.session_data is data
        target = manager.create_session(model='foreign/unavailable', working_dir=tmp_path)
        target.messages = [UserMessage(content='must not activate')]
        manager.save_session(target)
        with pytest.raises(ValueError, match='no fallback'):
            session.switch_session(target.session_id)
        assert session.agent.state == before and session.extensions is extensions and session.session_data is data
        data.branches.append({'branch_id': 'bad-model', 'model': 'foreign/unavailable', 'messages': [], 'plan': None})
        with pytest.raises(ValueError, match='no fallback'):
            session.restore_branch('bad-model')
        assert session.agent.state == before and session.extensions is extensions and session.session_data is data
    finally:
        session.dispose()


@pytest.mark.parametrize('selection', ['missing', 'embedding', 'no-stream', 'bad-window', 'no-default'])
def test_startup_explicit_invalid_profiles_refused_without_fallback_or_new_session(tmp_path, monkeypatch, selection):
    from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
    adapter = OwnedAdapter()
    class Provider:
        def models(self):
            return () if selection == 'no-default' else adapter.profiles
        def model(self, name):
            if name == 'missing':
                return None
            if name == 'embedding':
                return Model(id='owned/embedding', model_type='embedding', context_window=1000)
            if name == 'no-stream':
                return Model(id='owned/no-stream', supports_streaming=False, context_window=1000)
            if name == 'bad-window':
                return Model(id='owned/bad-window', context_window=0)
            return adapter.resolve_model(name)
        def stream(self, *args):
            raise AssertionError('Profile validation must not call a model')
    monkeypatch.setenv('CODING_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr('xdog.ai.load', lambda: Provider())
    with pytest.raises(ValueError, match='no fallback'):
        create_agent_session(CreateSessionOptions(working_dir=tmp_path,
                             model_name=None if selection == 'no-default' else selection))
    assert not list((tmp_path / 'data' / 'sessions').glob('*.json'))


def test_resume_missing_profile_does_not_rewrite_saved_model_or_history(tmp_path, monkeypatch):
    from xdog.coding.core.sdk import CreateSessionOptions, create_agent_session
    home = tmp_path / 'data'
    monkeypatch.setenv('CODING_DIR', str(home))
    manager = SessionManager(home / 'sessions')
    saved = manager.create_session(model='owned/missing', working_dir=tmp_path)
    saved.messages = [UserMessage(content='SAVED-HISTORY-MUST-SURVIVE')]
    manager.save_session(saved)
    original = manager._session_path(saved.session_id).read_bytes()
    adapter = OwnedAdapter()
    class Provider:
        def models(self):
            return adapter.profiles
        def model(self, name):
            return adapter.resolve_model(name)
        def stream(self, *args):
            raise AssertionError('No startup model call')
    monkeypatch.setattr('xdog.ai.load', lambda: Provider())
    with pytest.raises(ValueError, match='no fallback'):
        create_agent_session(CreateSessionOptions(working_dir=tmp_path, resume_id=saved.session_id))
    assert manager._session_path(saved.session_id).read_bytes() == original


def test_opaque_sdk_adapter_does_not_borrow_global_capability_metadata(tmp_path, monkeypatch):
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(lambda *args: EventStream.empty(AssistantMessage()),
                                      config=AgentConfig(model='opaque/first')),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_repo=None, bash=None, model_info=OwnedAdapter().profiles[0], max_prompt_tokens=24000)
    try:
        monkeypatch.setattr('xdog.ai.load',
                            lambda: (_ for _ in ()).throw(AssertionError('must not consult global catalog')))
        session.set_model('opaque/second')
        assert session.model == 'opaque/second' and session.model_info is None
        assert session.max_prompt_tokens == 0 and session.agent.supports_tool_calls is None
        assert session.model_catalog() == ()
    finally:
        session.dispose()


def test_unsupported_effort_and_invalid_cached_profile_are_denied_before_selection(tmp_path):
    adapter = OwnedAdapter()
    manager = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(adapter, config=AgentConfig(model='owned/first')),
        session_data=manager.create_session(working_dir=tmp_path), session_manager=manager,
        settings=SettingsManager(global_path=tmp_path / 'settings.json'), working_dir=tmp_path,
        tool_repo=None, bash=None, model_info=adapter.profiles[0])
    try:
        session.set_model('first')
        session.set_thinking_level('high')
        original_state, original_options = session.agent.state, session.agent.options
        adapter.profiles = (*adapter.profiles, Model(id='owned/restricted', context_window=4000, reasoning=True,
                                                    supported_efforts=('low',)))
        with pytest.raises(ValueError, match='thinking level'):
            session.set_model('owned/restricted')
        assert session.agent.state == original_state and session.agent.options == original_options
        saved = manager.create_session(model='owned/restricted', working_dir=tmp_path)
        saved.settings = {'thinking_level': 'high'}
        manager.save_session(saved)
        owner = session.session_data
        with pytest.raises(ValueError, match='saved thinking'):
            session.switch_session(saved.session_id)
        assert session.session_data is owner and session.agent.state == original_state
        adapter.profiles = (*adapter.profiles, Model(id='owned/not-chat', model_type='embedding', context_window=4000))
        with pytest.raises(ValueError, match='streaming chat'):
            session.set_model('owned/not-chat')
        assert session.agent.state == original_state
    finally:
        session.dispose()
