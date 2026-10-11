from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.ai.types import AssistantMessage, UserMessage
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode

FIXTURE = Path(__file__).parents[2] / "agent/tests/fixtures/extensions"


@pytest.mark.parametrize("scope", ["sessions", "tree", "fail"])
def test_coding_command_session_browser_deferred_until_success_without_activation(tmp_path, scope):
    root = tmp_path / "extensions"
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"extensions": ["navigator"], "extension_dir": str(root)}))
    store = SessionManager(tmp_path / "sessions")
    calls = []

    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())

    session = AgentSession(
        agent=Agent(stream, config=AgentConfig(model="test/model")),
        session_data=store.create_session(working_dir=tmp_path),
        session_manager=store,
        settings=SettingsManager(global_path=settings),
        working_dir=tmp_path,
        tool_registry=None,
        bash=None,
        permissions=PermissionManager("ask"),
    )
    session.agent.append_message(UserMessage(content="retained tree checkpoint"))
    session._persist()
    ui = InteractiveMode(session)

    def wait(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            ui._poll()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Command navigation did not settle")

    try:
        before = session.session_id
        ui._editor.set_text("parent draft")
        ui._handle_slash_command(
            "ext-navigator-browse", "tree" if scope == "tree" else scope if scope == "fail" else ""
        )
        wait(lambda: ui._command is None)
        if scope == "fail":
            assert ui._navigation_panel is None and ui._extension_browser is None
        else:
            assert ui._navigation_panel is not None and ui._tui._focused is ui._navigation_panel
            wait(lambda: not ui._navigation_provider.snapshot().busy)
            assert ui._navigation_provider.snapshot().entries
            if scope == "tree":
                assert ui._navigation_provider.snapshot().scope == f"tree|{before}|"
            ui._close_navigation()
            assert ui._tui._focused is ui._editor
        assert session.session_id == before and ui._editor.get_text() == "parent draft" and not calls
        assert session.messages[0].content == "retained tree checkpoint" and session.permissions.mode == "ask"
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()


def test_stale_deferred_browser_cannot_act_on_a_different_command_or_history_epoch(tmp_path):
    from unittest.mock import MagicMock
    session = MagicMock()
    session.session_id = 'current'
    session.working_dir = tmp_path
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('draft')
        ui._extension_browser = ('old-command', ui._mcp_context_key(), 'sessions')
        ui._finish_extension_browser('new-command', True)
        assert ui._navigation_panel is None and ui._extension_browser is None
        ui._extension_browser = ('command', ui._mcp_context_key(), 'tree')
        ui._output_epoch += 1
        ui._finish_extension_browser('command', True)
        assert ui._navigation_panel is None and ui._editor.get_text() == 'draft'
        session.agent.prompt.assert_not_called()
    finally:
        ui._request_exit()


@pytest.mark.parametrize('measured', [False, True])
def test_coding_inspection_command_uses_actual_model_context_limits_and_persists_without_model_call(tmp_path, measured):
    from xdog.ai.types import Model, Usage

    root = tmp_path / 'extensions'
    shutil.copytree(FIXTURE, root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['navigator'], 'extension_dir': str(root)}))
    store = SessionManager(tmp_path / 'sessions')
    calls = []
    def stream(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    model = Model(id='test/model', name='Actual model', provider='owned', context_window=64000,
                  max_prompt_tokens=48000, reasoning=True, supported_efforts=('low', 'high'), input=('text', 'image'),
                  headers={'Authorization': 'PRIVATE-HEADER'}, base_url='https://PRIVATE-ENDPOINT.invalid')
    session = AgentSession(agent=Agent(stream, config=AgentConfig(model='test/model', context_window=64000,
                                                               max_prompt_tokens=48000)),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path, tool_registry=None, bash=None,
        model_info=model, context_window=64000, max_prompt_tokens=48000, permissions=PermissionManager('ask'))
    if measured:
        session.agent.append_message(AssistantMessage(model='test/model', usage=Usage(input=100, output=20,
                                                                                    cache_read=30, cache_write=40)))
        session.agent.append_message(UserMessage(content='later input'))
    ui = InteractiveMode(session)
    try:
        ui._editor.set_text('unchanged parent draft')
        ui._handle_slash_command('ext-navigator-inspect', '')
        deadline = time.monotonic() + 8
        while ui._command is not None and time.monotonic() < deadline:
            ui._poll()
            time.sleep(0.003)
        assert ui._command is None
        value = session.extensions.entries.for_extension('navigator')[-1]['data']
        assert value['model']['id'] == 'test/model' and value['model']['name'] == 'Actual model'
        assert value['model']['supported_efforts'] == ['low', 'high']
        assert value['usage']['context_limit'] == 48000
        assert value['usage']['tokens'] == (190 if measured else None)
        assert value['usage']['source'] == ('last-response' if measured else 'unavailable')
        assert value['usage']['messages_since_usage'] == (1 if measured else None)
        assert 'PRIVATE-' not in json.dumps(value)
        assert value['model']['session_id'] == session.session_id == value['usage']['session_id']
        persisted = store.load_session(session.session_id)
        assert persisted.extension_entries['navigator'][-1]['data'] == value
        assert 'SESSION-INSPECTION' in '\n'.join(ui._chat_log.render(200))
        assert ui._editor.get_text() == 'unchanged parent draft' and ui._tui._focused is ui._editor
        assert not calls and session.permissions.mode == 'ask'
    finally:
        ui._request_exit()
        asyncio.run(session.extensions.aclose())
        session.dispose()
