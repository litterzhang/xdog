from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from xdog.agent import Agent, AgentConfig
from xdog.agent.core import AgentTool, AgentToolResult
from xdog.ai.types import AssistantMessage, TextContent
from xdog.ai.utils.event_stream import EventStream
from xdog.coding.core.agent_session import AgentSession
from xdog.coding.core.permissions import PermissionManager
from xdog.coding.core.session_manager import SessionManager
from xdog.coding.core.settings_manager import SettingsManager
from xdog.coding.modes.interactive.interactive_mode import InteractiveMode
from xdog.tui.keys import KeyEvent


@pytest.mark.parametrize('model_id', ['echo', 'tool', 'failure', 'aborted'])
async def test_coding_actual_registered_provider_selection_stream_permissions_and_no_builtin_fallback(
        tmp_path, model_id):
    tool = model_id == 'tool'
    failure = model_id in {'failure', 'aborted'}
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['providermock'], 'extension_dir': str(root)}))
    calls, mutations = [], []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    async def mutate(call, params, *args, **kwargs):
        mutations.append(params)
        return AgentToolResult(content=(TextContent(text='changed'),))
    custom = AgentTool(name='mutate', description='Mutating test operation', parameters={
        'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']}, execute=mutate)
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(base, config=AgentConfig(model='base/model'), tools=(custom,)),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('ask'))
    ui = InteractiveMode(session)
    async def wait(predicate):
        async with asyncio.timeout(8):
            while not predicate():
                ui._poll()
                await asyncio.sleep(0.003)
    try:
        ui._handle_slash_command('extensions', '')
        await wait(lambda: ui._command is None)
        ui._handle_slash_command('model', '')
        await wait(lambda: ui._command is None)
        assert 'ext-providermock-local/echo' in '\n'.join(ui._chat_log.render(120))
        alias = 'ext-providermock-local/' + model_id
        ui._handle_slash_command('model', alias)
        await wait(lambda: ui._command is None)
        assert session.model == alias and session.agent.context_window == 32000
        ui._handle_submit('run selected provider')
        if tool:
            await wait(lambda: ui._permission_prompt is not None)
            ui._permission_prompt.handle_input(KeyEvent('escape'))
        await wait(lambda: not ui._worker_active and not ui._is_busy
                   and any(message.role == 'assistant' for message in session.agent.messages))
        assert not calls and not mutations and session.permissions.mode == 'ask'
        text = '\n'.join(ui._chat_log.render(120))
        assert ('EXT-PROVIDER-PARTIAL' if failure else 'EXT-PROVIDER-ANSWER') in text
        if failure:
            diagnostic = 'Provider canceled' if model_id == 'aborted' else 'Provider unavailable'
            assert ('Canceled: ' if model_id == 'aborted' else 'Error: ') + diagnostic in text
            assert text.count(diagnostic) == 1 and ui._permission_prompt is None
            assert not ui._chat_log._streaming
            assert session.agent.messages[-1].stop_reason == ('aborted' if model_id == 'aborted' else 'error')
            assert await session.extensions.command('ext-providermock-closed', '') == '1'
        assert await session.extensions.command('ext-providermock-count', '') == ('2' if tool else '1')
        saved = store.load_session(session.session_id)
        assert saved.model == alias
        if failure:
            assert saved.messages[-1].error_message == diagnostic
            assert saved.messages[-1].response_id == 'owned-failure'
        ui._handle_slash_command('extensions', 'unload providermock')
        await wait(lambda: ui._command is None)
        ui._handle_submit('unavailable provider must not route to base')
        await wait(lambda: not ui._worker_active)
        assert not calls
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()


@pytest.mark.parametrize('model_id', ['echo', 'failure', 'aborted'])
async def test_coding_child_provider_override_uses_independent_worker_without_parent_routing_change(tmp_path, model_id):
    from xdog.agent.supervision_config import ChildConfig
    root = tmp_path / 'extensions'
    shutil.copytree(Path(__file__).parents[2] / 'agent/tests/fixtures/extensions', root)
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'extensions': ['providermock'], 'extension_dir': str(root)}))
    calls = []
    def base(*args):
        calls.append(args)
        return EventStream.empty(AssistantMessage())
    store = SessionManager(tmp_path / 'sessions')
    session = AgentSession(agent=Agent(base, config=AgentConfig(model='base/model')),
        session_data=store.create_session(working_dir=tmp_path), session_manager=store,
        settings=SettingsManager(global_path=settings), working_dir=tmp_path,
        tool_registry=None, bash=None, permissions=PermissionManager('ask'))
    ui = InteractiveMode(session)
    try:
        await session.initialize_extensions()
        child = await session.children.supervisor.start('provider child', config=ChildConfig(
            model='ext-providermock-local/' + model_id, max_model_calls=2).to_dict())
        record = await session.children.supervisor.control('wait', child)
        expected = 'canceled' if model_id == 'aborted' else 'failed' if model_id == 'failure' else 'completed'
        assert record['status'] == expected
        assert ('EXT-PROVIDER-ANSWER' if model_id == 'echo' else 'EXT-PROVIDER-PARTIAL') in record['result']
        assert record['effective']['model'] == 'ext-providermock-local/' + model_id and record['calls'] == 1
        assert not calls and session.model == 'base/model'
        assert await session.extensions.command('ext-providermock-count', '') == '0'
    finally:
        ui._request_exit()
        await session.children.supervisor.aclose()
        await session.extensions.aclose()
